"""One time: the payments from before the state's rolling three-year window, from an earlier
archive of the checkbook files (monthly CSVs, July 2020 on), into vendor-checkbook. Rows are
normalized the way the live files are and keyed the same way, so payments in both are one
record. Nothing is marked dropped."""

import csv
from collections import Counter
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from records import ingest, sources
from records.models import Run
from records.run import ensure_recordset
from records.sources.vendor_checkbook import parse
from records.sources.vendor_checkbook.source import STATE, Item

# The earlier archive renamed the agency column and added agency names
COLUMNS = ["document_date", "document_number", "vendor_name", "vendor_number",
           "vendor_group_number", "ap_payment_date", "voucher_number", "amt", "agency_code",
           "agency_name"]  # fmt: skip


# The earlier archive rewrote agency codes 032 and 20 as 03. Per the Bureau of Finance and
# Management: 03 used to be Agriculture and 20 Environment and Natural Resources; combined,
# they're DANR, 03. So an 03 from that archive may have been published as 03, 032 or 20.
AGENCY_03 = (
    "published as 03, 032 or 20: the earlier archive rewrote 032 and 20 as 03 "
    "(03 was Agriculture, 20 Environment and Natural Resources; combined, DANR is 03)"
)


class Command(BaseCommand):
    help = "Import an earlier archive of the vendor checkbook (a folder of monthly CSVs)"

    def add_arguments(self, parser):
        parser.add_argument("folder")

    def handle(self, *args, folder, **options):
        files = sorted(Path(folder).glob("*.csv"))
        if not files:
            raise CommandError(f"no CSVs in {folder}")
        recordset = ensure_recordset(sources.get("vendor-checkbook"))
        items, seen = [], Counter()
        for path in files:
            with path.open(newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                if reader.fieldnames != COLUMNS:
                    raise CommandError(f"{path.name}: columns are {reader.fieldnames}")
                for r in reader:
                    row = {
                        "agency": r["agency_code"], "document_date": r["document_date"] or None,
                        "document_number": r["document_number"],
                        "vendor_name": " ".join(r["vendor_name"].split()),
                        "vendor_number": r["vendor_number"],
                        "vendor_group_number": r["vendor_group_number"],
                        "ap_payment_date": r["ap_payment_date"],
                        "voucher_number": r["voucher_number"], "amount": parse.amount(r["amt"]),
                    }  # fmt: skip
                    base = parse.key(row)
                    seen[base] += 1
                    data = {**row, "agency_name": r["agency_name"] or None,
                            "fiscal_year": parse.fiscal_year(row["ap_payment_date"])}  # fmt: skip
                    if row["agency"] == "03":
                        data["agency_note"] = AGENCY_03
                    items.append(Item(key=parse.key(row, seen[base]), data=data,
                                      source_url=f"file:{path.name}", places=[STATE]))  # fmt: skip
        current = set(recordset.records.values_list("key", flat=True))
        new = [i for i in items if i.key not in current]
        checks = {"imported_from": "an earlier archive of the checkbook files",
                  "files": len(files), "rows": len(items),
                  "already_stored": len(items) - len(new)}  # fmt: skip
        run = Run.objects.create(recordset=recordset, checks=checks)
        stored = ingest.store(run, new, complete=False)
        for k, v in stored.counts().items():
            setattr(run, k, v)
        run.fetched_count, run.status = len(items), Run.Status.SUCCEEDED
        run.save()
        self.stdout.write(self.style.SUCCESS(
            f"{len(items):,} rows from {len(files)} files: {stored.counts()['added']:,} added, "
            f"{len(items) - len(new):,} already stored"))  # fmt: skip
