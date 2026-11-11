"""State vendors: a real slice of the vendor file."""

from datetime import date
from pathlib import Path

from records.models import Record
from records.run import run_source
from records.sources.state_vendors import source

FILE = (Path(__file__).parent / "fixtures" / "VendorPmts.csv").read_bytes()


def test_rows_name_their_fiscal_years():
    rows = source.rows(FILE, date(2026, 11, 10))
    assert len(rows) == 20
    assert set(rows[0]["paid"]) == {"2027", "2026", "2025"}
    assert rows[0]["vendor_name"] == "101 TECHNOLOGIES INC" and rows[0]["state"] == "SD"


class FixtureFetcher:
    store = None

    def request(self, method, url, **kwargs):
        class Resp:
            content = FILE

        return Resp()


def test_first_run_makes_the_record_set(db, state, sd_places):
    run = run_source("state-vendors", fetcher=FixtureFetcher())
    assert run.added == 20
    assert Record.objects.get(key="12587765").data["city"] == "BONESTEEL"
