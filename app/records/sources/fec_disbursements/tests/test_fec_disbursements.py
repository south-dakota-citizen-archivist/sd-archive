"""FEC Schedule B for South Dakota's committees: a real API response (one committee and
cycle) read and tied to the recipients' South Dakota cities."""

from pathlib import Path

from django.utils import timezone

from records.models import Record, RecordSet
from records.run import run_source
from records.sources._fec_testing import FixtureFetcher

FIXTURES = Path(__file__).parent / "fixtures"
PAGES = [FIXTURES / "schedule_b-1.json", FIXTURES / "schedule_b-2.json"]


def test_run(db, sd_places):
    committees = RecordSet.objects.create(slug="fec-committees", name="FEC committees",
                                          dataset="fec")  # fmt: skip
    Record.objects.create(recordset=committees, key="C00628917", first_seen=timezone.now(),
                          data={"committee_id": "C00628917", "cycles": [2016]})  # fmt: skip
    RecordSet.objects.create(slug="fec-disbursements", name="FEC disbursements", dataset="fec")
    run = run_source("fec-disbursements",
                     fetcher=FixtureFetcher({"/schedules/schedule_b/": PAGES}))  # fmt: skip
    assert run.added == 3
    paid = Record.objects.get(key="4020320171369526969")
    assert paid.data["recipient_name"] == "JOHNSON, DUSTY"
    assert paid.data["disbursement_amount"] == 516.09
    assert paid.data["committee_name"] == "FRIENDS OF DUSTY JOHNSON"
    assert sorted(paid.places.values_list("short_name", flat=True)) == [
        "Mitchell", "South Dakota"]  # fmt: skip
    anedot = Record.objects.get(key="4020320171369526967")
    assert list(anedot.places.values_list("short_name", flat=True)) == ["South Dakota"]
