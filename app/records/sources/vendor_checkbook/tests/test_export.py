"""The vendor checkbook's Parquet files: one per fiscal year, dropped rows apart."""

from io import StringIO

import pyarrow.parquet as pq
from django.core.management import call_command

from records.run import run_source
from records.sources.vendor_checkbook.tests.test_vendor_checkbook import FixtureFetcher


def test_export(db, state, tmp_path):
    run_source("vendor-checkbook", fetcher=FixtureFetcher())
    call_command("export_vendor_checkbook", "--out", str(tmp_path), stdout=StringIO())
    names = sorted(p.name for p in tmp_path.iterdir())
    years = [f"vendor-checkbook-FY{y}.parquet" for y in (2025, 2026, 2027)]
    assert names == [*years, "vendor-checkbook-dropped.parquet"]
    fy27 = pq.read_table(tmp_path / "vendor-checkbook-FY2027.parquet")
    assert fy27.num_rows == 40 and str(fy27.schema.field("amount").type) == "decimal128(14, 2)"
    assert set(fy27.column("fiscal_year").to_pylist()) == {2027}
