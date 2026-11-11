"""The vendor checkbook as files: one Parquet file per fiscal year of payments as currently
published (vendor-checkbook-FY2027.parquet, …), and one of the rows the state no longer
publishes as they were (corrected or withdrawn: vendor-checkbook-dropped.parquet), so a year's
file sums to what the state shows."""

from collections import defaultdict
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from django.conf import settings
from django.core.management.base import BaseCommand

from records.models import Record

FIELDS = ["fiscal_year", "ap_payment_date", "agency", "agency_name", "document_date",
          "document_number", "voucher_number", "vendor_number", "vendor_group_number",
          "vendor_name", "amount", "agency_note"]  # fmt: skip
SCHEMA = pa.schema([
    ("key", pa.string()), ("fiscal_year", pa.int16()), ("ap_payment_date", pa.date32()),
    ("agency", pa.string()), ("agency_name", pa.string()), ("document_date", pa.date32()),
    ("document_number", pa.string()), ("voucher_number", pa.string()),
    ("vendor_number", pa.string()), ("vendor_group_number", pa.string()),
    ("vendor_name", pa.string()), ("amount", pa.decimal128(14, 2)), ("agency_note", pa.string()),
    ("first_seen", pa.timestamp("s", tz="UTC")), ("dropped_at", pa.timestamp("s", tz="UTC")),
])  # fmt: skip


def _row(key, data, first_seen, dropped_at):
    from datetime import date

    def day(v):
        return date.fromisoformat(v) if v else None

    return {"key": key, **{f: data.get(f) for f in FIELDS},
            "ap_payment_date": day(data.get("ap_payment_date")),
            "document_date": day(data.get("document_date")),
            "amount": Decimal(data["amount"]) if data.get("amount") else None,
            "first_seen": first_seen, "dropped_at": dropped_at}  # fmt: skip


class Command(BaseCommand):
    help = "Write the vendor checkbook as Parquet files, one per fiscal year"

    def add_arguments(self, parser):
        parser.add_argument("--out", default=str(Path(settings.MEDIA_ROOT) / "exports" /
                                                 "vendor-checkbook"))  # fmt: skip

    def handle(self, *args, out, **options):
        out = Path(out)
        out.mkdir(parents=True, exist_ok=True)
        years, dropped = defaultdict(list), []
        qs = Record.objects.filter(recordset__slug="vendor-checkbook").order_by("key")
        for key, data, first_seen, dropped_at in qs.values_list(
            "key", "data", "first_seen", "dropped_at"
        ).iterator(chunk_size=20000):
            row = _row(key, data, first_seen, dropped_at)
            (dropped if dropped_at else years[data["fiscal_year"]]).append(row)
        written = []
        for name, rows in [
            *((f"FY{y}", r) for y, r in sorted(years.items())),
            ("dropped", dropped),
        ]:
            path = out / f"vendor-checkbook-{name}.parquet"
            pq.write_table(pa.Table.from_pylist(rows, schema=SCHEMA), path, compression="zstd")
            written.append((path.name, len(rows), path.stat().st_size))
            self.stdout.write(
                f"  {path.name}: {len(rows):,} rows, {path.stat().st_size / 1e6:.1f} MB"
            )
        self.stdout.write(self.style.SUCCESS(f"Wrote {len(written)} files to {out}"))
