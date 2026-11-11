"""Import the public-records-liberator's database: record sets, records, documents and the
extractions people reviewed. Files stay where they are; documents point at them by hash.

Keys are provisional: the record's URL where it's unique in its record set, else its first
document's URL, else a hash of its data. Each source's port sets the real key. Every row keeps
its old ID in `legacy`, so ported sources can be compared with what was imported.

Running it again replaces what an earlier import wrote and nothing else.
"""

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

import psycopg
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils.text import slugify

from census.models import Publisher
from records.models import Document, Extraction, Record, RecordSet

# PRL record set name → (slug, catalog dataset id)
RECORD_SETS = {
    "Newspaper Circulation Statements": ("newspaper-circulation", "newspaper-circulation"),
    "Boards and Commissions Members": ("boards-commissions-members", "boards-commissions"),
    "State Employee Phone Directory": ("state-employee-directory", "state-employee-directory"),
    "State Employee Salaries": ("state-employee-salaries", "state-employee-salaries"),
    "Farm, Ranch, and Home Name Registrations": ("farm-ranch-names", "farm-ranch-names"),
    "Private Lobbyist Registrations": ("private-lobbyists", "lobbyists"),
    "Public Lobbyist Registrations": ("public-lobbyists", "lobbyists"),
    "Athletic Agent Registrations": ("athletic-agents", "athletic-agents"),
    "Campaign Finance Filings": ("campaign-finance-filings", "campaign-finance-filings"),
    "Candidate Financial Interest Statements": (
        "candidate-financial-interest", "candidate-financial-interest"),
    "Communication Expenditures": ("communication-expenditures", "communication-expenditures"),
    "Executive Orders": ("executive-orders", "executive-orders"),
    "Gubernatorial Proclamations": ("proclamations", "proclamations"),
    "Elected Official Financial Interest Statements": (
        "elected-official-financial-interest", "elected-official-financial-interest"),
    "Local Government Bond Information Statements": (
        "local-bond-statements", "local-bond-statements"),
    "Oaths of Office": ("oaths-of-office", "oaths-of-office"),
    "Personality's Right of Publicity Registrations": ("publicity-rights", "publicity-rights"),
    "Statewide Raffle Requests": ("raffle-requests", "raffle-requests"),
    "Supplemental Campaign Finance Statements": (
        "supplemental-campaign-finance", "supplemental-campaign-finance"),
    "Legislative Sessions": ("legislature-sessions", "legislature"),
    "Legislators by Term": ("legislature-legislators-by-term", "legislature"),
    "Historical Legislators": ("legislature-historical-legislators", "legislature"),
    "Bills": ("legislature-bills", "legislature"),
    "Committee Memberships": ("legislature-committee-memberships", "legislature"),
    "Bill Documents": ("legislature-bill-documents", "legislature"),
    "Roll Call Votes": ("legislature-roll-calls", "legislature"),
    "Session Laws": ("legislature-session-laws", "legislature"),
    "Interim Committee Memberships": ("legislature-interim-committees", "legislature"),
    "LRC Staff": ("legislature-lrc-staff", "legislature"),
    "FEC Candidates (South Dakota)": ("fec-candidates", "fec"),
    "FEC Committees (South Dakota)": ("fec-committees", "fec"),
    "FEC Contributions (Schedule A)": ("fec-contributions", "fec"),
    "FEC Disbursements (Schedule B)": ("fec-disbursements", "fec"),
    "FEC Independent Expenditures (Schedule E)": ("fec-independent-expenditures", "fec"),
}  # fmt: skip

DOC_KIND = {"native_pdf": "native", "image_pdf": "scanned", "image": "image", "unknown": "unknown"}
DOC_STATUS = {
    "PENDING": "pending", "DOWNLOADING": "pending", "DOWNLOADED": "stored",
    "CATEGORIZING": "stored", "CATEGORIZED": "categorized", "PROCESSING": "categorized",
    "PROCESSED": "extracted", "FAILED": "failed",
}  # fmt: skip
REVIEW = {"APPROVED": "approved", "REJECTED": "rejected", "NEEDS_CORRECTION": "pending",
          "PENDING": "pending"}  # fmt: skip
BATCH = 5000
# ExtractedRecords that are only copies of a direct-ingest record's data
COPIES_SQL = """SELECT recordset_id, source_url, data FROM scrapers_extractedrecord
WHERE coalesce(extraction_model, '') = '' AND reviewed_at IS NULL"""


def content_hash(data: dict) -> str:
    blob = json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def rows(conn, sql: str, params=()):
    with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute(sql, params)
        yield from cur


class Command(BaseCommand):
    help = "Import the public-records-liberator's database (records, documents, reviews)"

    def add_arguments(self, parser):
        parser.add_argument("--database", default="sd_scraper_fleet")
        parser.add_argument(
            "--media", type=Path,
            default=Path.home() / "civic-tech" / "public-records-liberator" / "media",
            help="PRL's media folder, recorded on each document",
        )  # fmt: skip

    def handle(self, *args, database: str, media: Path, **options):
        with psycopg.connect(dbname=database) as conn:
            report = self.run(conn, media)
        for line in report:
            self.stdout.write(line)

    @transaction.atomic
    def run(self, conn, media: Path) -> list[str]:
        report = []
        # Publishers (custodians), parents first
        custodians = list(rows(conn, "SELECT id, name, parent_id FROM scrapers_recordscustodian"))
        publishers = {}
        for c in sorted(custodians, key=lambda c: c["parent_id"] is not None):
            pub, _ = Publisher.objects.get_or_create(
                name=c["name"], defaults={"slug": slugify(c["name"])[:255]}
            )
            if c["parent_id"] and c["parent_id"] in publishers:
                pub.parent = publishers[c["parent_id"]]
                pub.save(update_fields=["parent"])
            publishers[c["id"]] = pub

        # Record sets
        sets = {}
        unknown = []
        doc_counts = {
            r["recordset_id"]: r["n"]
            for r in rows(conn, "SELECT r.recordset_id, count(d.id) AS n FROM scrapers_record r "
                                "JOIN scrapers_document d ON d.record_id = r.id GROUP BY 1")
        }  # fmt: skip
        for rs in rows(conn, "SELECT * FROM scrapers_recordset ORDER BY id"):
            if rs["name"] not in RECORD_SETS:
                unknown.append(rs["name"])
                continue
            slug, dataset = RECORD_SETS[rs["name"]]
            sets[rs["id"]], _ = RecordSet.objects.update_or_create(
                slug=slug,
                defaults={
                    "name": rs["name"], "dataset": dataset,
                    "description": rs["description"] or "", "base_url": rs["base_url"] or "",
                    "publisher": publishers.get(rs["custodian_id"]),
                    "ingest": "documents" if doc_counts.get(rs["id"]) else "rows",
                },
            )  # fmt: skip
        if unknown:
            raise ValueError(f"record sets with no mapping: {unknown}")

        # Clear what an earlier import wrote (never rows a ported source wrote)
        imported = Record.objects.filter(
            recordset__in=sets.values(), legacy__has_key="prl_record_id"
        )
        Extraction.objects.filter(legacy__has_key="prl_extracted_record_id").delete()
        Document.objects.filter(record__in=imported).delete()
        imported.delete()

        # Records
        first_doc_url = {
            r["record_id"]: r["url"]
            for r in rows(conn, "SELECT DISTINCT ON (record_id) record_id, "
                                "coalesce(nullif(file_url, ''), source_url) AS url "
                                "FROM scrapers_document ORDER BY record_id, id")
        }  # fmt: skip
        url_counts = Counter(
            (r["recordset_id"], r["source_url"])
            for r in rows(conn, "SELECT recordset_id, source_url FROM scrapers_record")
        )
        records: dict[int, Record] = {}
        keys_seen: dict[tuple, int] = {}
        collisions: Counter = Counter()
        key_kind: Counter = Counter()
        batch = []
        for r in rows(conn, "SELECT * FROM scrapers_record ORDER BY id"):
            rs = sets[r["recordset_id"]]
            data = r["metadata"] or {}
            url = r["source_url"] or ""
            if url and url_counts[(r["recordset_id"], url)] == 1:
                key, kind = url, "source URL"
            elif first_doc_url.get(r["id"]):
                key, kind = first_doc_url[r["id"]], "document URL"
            else:
                key, kind = "data:" + content_hash(data)[:32], "data hash"
            if (rs.pk, key) in keys_seen:
                collisions[rs.slug] += 1
                key = f"{key}#prl-{r['id']}"
            keys_seen[(rs.pk, key)] = r["id"]
            key_kind[(rs.slug, kind)] += 1
            rec = Record(
                recordset=rs, key=key[:500], data=data, content_hash=content_hash(data),
                source_url=url[:2000], first_seen=r["created_at"],
                legacy={"prl_record_id": r["id"], "prl_status": r["status"]},
            )  # fmt: skip
            batch.append((r["id"], rec))
            if len(batch) >= BATCH:
                self._save_records(batch, records)
                batch = []
        self._save_records(batch, records)

        # Documents attached only to their record set (older newspaper statements): a record
        # each, keyed by the file's URL, so none is lost
        orphans = list(rows(conn, "SELECT * FROM scrapers_document WHERE record_id IS NULL "
                                  "ORDER BY id"))  # fmt: skip
        batch = []
        orphan_record = {}
        for d in orphans:
            rs = sets[d["recordset_id"]]
            key = (d["file_url"] or d["source_url"] or f"prl-document-{d['id']}")[:500]
            if (rs.pk, key) in keys_seen:
                key = f"{key}#prl-document-{d['id']}"[:500]
            keys_seen[(rs.pk, key)] = -d["id"]
            data = d["metadata"] or {}
            rec = Record(
                recordset=rs, key=key, data=data, content_hash=content_hash(data),
                source_url=(d["source_url"] or "")[:2000], first_seen=d["created_at"],
                legacy={"prl_record_id": None, "prl_document_id": d["id"]},
            )  # fmt: skip
            batch.append((d["id"], rec))
        Record.objects.bulk_create([r for _, r in batch], batch_size=BATCH)
        orphan_record = dict(batch)
        key_kind[("newspaper-circulation", "document URL (statement without a record)")] += len(
            orphans
        )

        # Documents
        documents: dict[int, Document] = {}
        batch = []
        for d in rows(conn, "SELECT * FROM scrapers_document ORDER BY id"):
            record = records.get(d["record_id"]) or orphan_record.get(d["id"])
            if record is None:
                continue
            filename = Path(d["file"]).name if d["file"] else ""
            doc = Document(
                record=record, url=(d["file_url"] or d["source_url"] or "")[:2000],
                sha256=d["content_hash"] or "", filename=filename[:500],
                mime_type=d["mime_type"] or "", size=d["file_size"], pages=d["page_count"],
                kind=DOC_KIND.get(d["document_type"] or "unknown", "unknown"),
                category=d["document_category"] or "",
                category_method="prl" if d["document_category"] else "",
                status=DOC_STATUS.get(d["status"], "pending"), error=d["error"] or "",
                metadata=d["metadata"] or {},
                legacy={"prl_document_id": d["id"], "prl_file": d["file"] or "",
                        "prl_media": str(media)},
            )  # fmt: skip
            batch.append((d["id"], doc))
            if len(batch) >= BATCH:
                self._save_documents(batch, documents)
                batch = []
        self._save_documents(batch, documents)

        # Extractions people reviewed or a model made (direct-ingest copies aren't extractions)
        extractions = 0
        for e in rows(conn, "SELECT * FROM scrapers_extractedrecord WHERE "
                            "coalesce(extraction_model, '') <> '' OR reviewed_at IS NOT NULL "
                            "OR review_status <> 'APPROVED'"):  # fmt: skip
            doc = documents.get(e["document_id"])
            if doc is None:
                report.append(f"  extraction {e['id']}: its document {e['document_id']} wasn't "
                              "imported")  # fmt: skip
                continue
            Extraction.objects.create(
                document=doc, schema=doc.record.recordset.slug, data=e["data"] or {},
                model=e["extraction_model"] or "", review=REVIEW.get(e["review_status"], "pending"),
                reviewed_at=e["reviewed_at"], review_notes=e["reviewer_notes"] or "",
                legacy={"prl_extracted_record_id": e["id"], "prl_raw_response": e["raw_response"]},
            )  # fmt: skip
            extractions += 1

        # How the direct-ingest copies compare with their records' data
        copies = list(rows(conn, COPIES_SQL))
        by_url = defaultdict(list)
        for rec in records.values():
            by_url[(rec.recordset_id, rec.source_url)].append(rec.data)
        same = 0
        for c in copies:
            same += c["data"] in by_url.get((sets[c["recordset_id"]].pk, c["source_url"]), [])

        report.append(f"{len(sets)} record sets, {len(records) + len(orphan_record):,} records, "
                      f"{len(documents):,} documents, {extractions} extractions")  # fmt: skip
        report.append(f"direct-ingest copies not imported: {len(copies):,} ({same:,} equal "
                      "their record's data; the rest are older snapshots of it)")  # fmt: skip
        for (slug, kind), n in sorted(key_kind.items()):
            report.append(f"  key {slug}: {n:,} by {kind}")
        for slug, n in collisions.most_common():
            report.append(f"  {slug}: {n:,} records share a key with another (kept, suffixed)")
        return report

    def _save_records(self, batch, into):
        Record.objects.bulk_create([r for _, r in batch], batch_size=BATCH)
        into.update(dict(batch))

    def _save_documents(self, batch, into):
        Document.objects.bulk_create([d for _, d in batch], batch_size=BATCH)
        into.update(dict(batch))
