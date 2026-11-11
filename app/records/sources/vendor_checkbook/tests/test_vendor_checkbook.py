"""State vendor payments: real slices of the three checkbook files and the agency list."""

from pathlib import Path

import pytest

from records.models import Record, RecordSet
from records.run import run_source
from records.sources.vendor_checkbook import parse

FIXTURES = Path(__file__).parent / "fixtures"


def file(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def test_rows():
    rows = parse.rows(file("CheckbookDetailCurrentYear.csv"))
    assert len(rows) == 40
    first = rows[0]
    assert set(first) == {"agency", "document_date", "document_number", "vendor_name",
                          "vendor_number", "vendor_group_number", "ap_payment_date",
                          "voucher_number", "amount"}  # fmt: skip
    assert first["agency"] == "010" and first["ap_payment_date"][4] == "-"
    assert all(r["amount"].count(".") == 1 and len(r["amount"].split(".")[1]) == 2 for r in rows)
    with pytest.raises(parse.LayoutChanged):
        parse.rows(b"agency,amount\n010,1.00\n")


def test_keys_and_years():
    row = parse.rows(file("CheckbookDetailPriorYear2.csv"))[0]
    assert parse.key(row).startswith(
        f"{row['ap_payment_date']} {row['agency']} {row['voucher_number']} "
    )
    assert parse.key({**row, "amount": parse.amount("88.0")}) == parse.key(
        {**row, "amount": parse.amount("88.00")}
    ), "a spreadsheet's 88.0 is the file's 88.00"
    assert parse.key(row, 2) == parse.key(row) + "-2", "an identical second row is its own record"
    assert parse.fiscal_year("2026-07-01") == 2027 and parse.fiscal_year("2026-06-30") == 2026


def test_agencies():
    names = parse.agencies(file("contactinfo.html"))
    assert names["010"] == "GOVERNOR'S OFFICE" and len(names) > 20


class FixtureFetcher:
    store = None

    def request(self, method, url, **kwargs):
        class Resp:
            content = file("contactinfo.html" if "contactinfo" in url else url.rsplit("/", 1)[1])

        return Resp()


def test_run_covers_only_the_years_in_the_files(db, state):
    rs = RecordSet.objects.create(slug="vendor-checkbook", name="Vendor payments",
                                  dataset="vendor-checkbook")  # fmt: skip

    def stored(key):
        return Record.objects.create(recordset=rs, key=key, content_hash="x",
                                     first_seen="2026-01-01T00:00Z")  # fmt: skip

    old, gone = stored("2021-03-01 11 1 abc"), stored("2025-03-01 11 1 abc")
    run = run_source("vendor-checkbook", fetcher=FixtureFetcher())
    assert run.added == 120 and run.checks["fiscal_years"] == [2025, 2026, 2027]
    old.refresh_from_db()
    gone.refresh_from_db()
    assert old.dropped_at is None, "a payment from before the window stays"
    assert gone.dropped_at is not None, "a payment missing from its year's file is dropped"
    paid = Record.objects.filter(recordset=rs, data__agency="010").first()
    assert paid.data["agency_name"] == "GOVERNOR'S OFFICE"
