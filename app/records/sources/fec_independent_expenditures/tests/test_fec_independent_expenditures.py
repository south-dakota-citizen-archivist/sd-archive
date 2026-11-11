"""FEC independent expenditures about South Dakota's races: a real API response, the cursor
followed to the end, and a run."""

from pathlib import Path

from records.models import Record, RecordSet
from records.run import run_source
from records.sources import _fec
from records.sources._fec_testing import FixtureFetcher

FIXTURES = Path(__file__).parent / "fixtures"
PAGES = [FIXTURES / "schedule_e-1.json", FIXTURES / "schedule_e-2.json"]


def test_the_cursor_follows_last_indexes():
    fetcher = FixtureFetcher({"/schedules/schedule_e/": PAGES})
    got = list(_fec.cursor(fetcher, "/schedules/schedule_e/", {"candidate_office_state": "SD"}))
    assert len(got) == 3
    second = fetcher.asked[1][1]
    assert second["last_index"] == "3062020110012802692"
    assert second["last_expenditure_date"] == "1979-11-08"


def test_run(db, state):
    RecordSet.objects.create(slug="fec-independent-expenditures", name="FEC IEs", dataset="fec")
    run = run_source("fec-independent-expenditures",
                     fetcher=FixtureFetcher({"/schedules/schedule_e/": PAGES}))  # fmt: skip
    assert run.added == 3
    ie = Record.objects.order_by("key").first()
    assert ie.data["candidate_name"] == "PRESSLER, LARRY" and ie.data["candidate_office"] == "S"
    assert ie.key == ie.data["sub_id"] and isinstance(ie.key, str)
