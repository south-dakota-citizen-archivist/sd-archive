"""The vendor checkbook's Parquet files: one per fiscal year, dropped rows apart."""

from io import StringIO

import pyarrow.parquet as pq
from django.core.management import call_command

from records.run import run_source
from records.sources.vendor_checkbook.tests.test_vendor_checkbook import FixtureFetcher


def test_export(db, state, tmp_path):
    run_source("vendor-checkbook", fetcher=FixtureFetcher())
    from records.models import Record, RecordSet

    unchecked = {"fiscal_year": 2021, "ap_payment_date": "2021-03-01", "amount": "1.00"}
    Record.objects.create(recordset=RecordSet.objects.get(slug="vendor-checkbook"),
                          key="2021-03-01 11 1 abc", content_hash="x", source_url="file:x.csv",
                          first_seen="2026-01-01T00:00Z", data=unchecked)  # fmt: skip
    call_command("export_vendor_checkbook", "--out", str(tmp_path), stdout=StringIO())
    names = sorted(p.name for p in tmp_path.iterdir())
    years = [f"vendor-checkbook-FY{y}.parquet" for y in (2025, 2026, 2027)]
    assert names == [*years, "vendor-checkbook-dropped.parquet"], "no unchecked FY2021"
    fy27 = pq.read_table(tmp_path / "vendor-checkbook-FY2027.parquet")
    assert fy27.num_rows == 40 and str(fy27.schema.field("amount").type) == "decimal128(14, 2)"
    assert set(fy27.column("fiscal_year").to_pylist()) == {2027}
