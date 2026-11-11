"""Bring in what an earlier system saw but no longer has as a record: its older snapshots of
a record set's items (the liberator's review-feed rows copied from earlier scrapes). Each one
whose item isn't among this record set's records, current or dropped, becomes a dropped
record in the source's shape (from_legacy), dated by the snapshot."""

from collections import defaultdict
from types import SimpleNamespace

import psycopg
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from records import sources
from records.management.commands.import_prl import RECORD_SETS, content_hash
from records.models import Record, RecordSet

SNAPSHOTS_SQL = """SELECT e.id, e.source_url, e.data, e.created_at
FROM scrapers_extractedrecord e JOIN scrapers_recordset rs ON rs.id = e.recordset_id
WHERE rs.name = %s AND coalesce(e.extraction_model, '') = '' AND e.reviewed_at IS NULL
ORDER BY e.created_at, e.id"""


class Command(BaseCommand):
    help = "Import an earlier system's snapshots of items no longer kept, as dropped records"

    def add_arguments(self, parser):
        parser.add_argument("slug", help="record set slug")
        parser.add_argument("--database", default="sd_scraper_fleet")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, slug, database, dry_run, **options):
        names = [name for name, (s, _) in RECORD_SETS.items() if s == slug]
        if not names:
            raise CommandError(f"{slug} isn't a record set from the liberator")
        source = sources.get(slug)
        if not (source.legacy_key and source.from_legacy):
            raise CommandError(f"{slug}: the source needs legacy_key and from_legacy")
        recordset = RecordSet.objects.get(slug=slug)
        with psycopg.connect(dbname=database) as conn:
            rows = conn.execute(SNAPSHOTS_SQL, names).fetchall()
        known = set(recordset.records.values_list("key", flat=True))
        new: dict[str, list] = defaultdict(list)
        for pid, url, data, created in rows:
            k = source.legacy_key(SimpleNamespace(data=data, key=url or ""))
            if k not in known:
                new[k].append((pid, url, data, created))
        self.stdout.write(f"{len(rows)} snapshots; {len(new)} item(s) not among the "
                          f"{len(known)} records")  # fmt: skip
        for k in list(new)[:10]:
            self.stdout.write(f"  {k}")
        if dry_run:
            return
        now = timezone.now()
        with transaction.atomic():
            Record.objects.bulk_create([
                Record(recordset=recordset, key=k, data=source.from_legacy(snaps[-1][2]),
                       content_hash=content_hash(snaps[-1][2]), source_url=snaps[-1][1] or "",
                       first_seen=snaps[0][3], dropped_at=now,
                       legacy={"prl_snapshot_ids": [s[0] for s in snaps],
                               "last_seen": snaps[-1][3].isoformat()})
                for k, snaps in new.items()
            ], batch_size=2000)  # fmt: skip
        self.stdout.write(self.style.SUCCESS(f"Added {len(new)} dropped record(s)."))
