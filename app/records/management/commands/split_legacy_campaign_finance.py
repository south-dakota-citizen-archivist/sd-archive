"""One time: the earlier archive kept every filing from the pre-2018 campaign finance system
as a document of a single catch-all record (the search page). Each becomes its own record in
campaign-finance-legacy, keyed by DocGuid, with its document moved to it; the catch-all goes.
"""

import psycopg
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from entities.mentions import replace
from records.models import Document, Record, RecordSet
from records.sources.campaign_finance_legacy import source

CATCHALL = "https://sdsos.gov/elections-voting/campaign-finance/search/"


class Command(BaseCommand):
    help = "Split the old campaign finance system's filings into one record each (one time)"

    def add_arguments(self, parser):
        parser.add_argument("--database", default="sd_scraper_fleet")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, database, dry_run, **options):
        current = RecordSet.objects.get(slug="campaign-finance-filings")
        try:
            catchall = current.records.get(key=CATCHALL)
        except Record.DoesNotExist:
            raise CommandError("no catch-all record: already split?") from None
        docs = list(catchall.documents.all())
        ids = [d.legacy.get("prl_document_id") for d in docs]
        with psycopg.connect(dbname=database) as conn:
            created = dict(conn.execute(
                "SELECT id, created_at FROM scrapers_document WHERE id = ANY(%s)", [ids]
            ).fetchall())  # fmt: skip
        self.stdout.write(f"{len(docs)} filings on the catch-all record")
        if dry_run:
            return
        with transaction.atomic():
            legacy, _ = RecordSet.objects.get_or_create(slug="campaign-finance-legacy", defaults={
                "name": "Campaign finance filings, 2000–2016 system",
                "dataset": current.dataset, "ingest": RecordSet.Ingest.DOCUMENTS,
                "publisher": current.publisher,
                "base_url": CATCHALL,
                "description": "Filings from the Secretary of State's campaign finance system "
                               "before sdcfr.sdsos.gov: one record per filing.",
            })  # fmt: skip
            records = []
            for d in docs:
                data = source.row(d.metadata, d.url)
                records.append(Record(
                    recordset=legacy, key=data["doc_guid"], data=data, source_url=d.url,
                    content_hash=d.sha256 or data["doc_guid"],
                    first_seen=created.get(d.legacy.get("prl_document_id")) or catchall.first_seen,
                    legacy={"prl_document_id": d.legacy.get("prl_document_id"),
                            "split_from": {"key": catchall.key, **catchall.legacy}},
                ))  # fmt: skip
            Record.objects.bulk_create(records, batch_size=2000)
            by_key = dict(legacy.records.values_list("key", "pk"))
            for d in docs:
                Document.objects.filter(pk=d.pk).update(record_id=by_key[source.doc_guid(d.url)],
                                                        updated_at=timezone.now())  # fmt: skip
            catchall.delete()
            places = Record.places.through
            from places.models import Jurisdiction

            state = Jurisdiction.objects.get(geoidfq=source.STATE)
            places.objects.bulk_create([places(record_id=pk, jurisdiction_id=state.pk)
                                        for pk in by_key.values()], batch_size=2000)  # fmt: skip
            found = [m for r in legacy.records.all() for m in source.mentions(r)]
            replace(legacy.dataset, found, keys=[f"{legacy.slug}:{k}" for k in by_key])
        self.stdout.write(
            self.style.SUCCESS(
                f"{len(records)} filing records in campaign-finance-legacy, {len(found)} mentions"
            )
        )
