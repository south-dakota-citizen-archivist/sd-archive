"""FEC Schedule A for South Dakota's committees: a real API response (one committee and
cycle) read, tied to the contributors' South Dakota cities, and a later run that drops only
transactions of the committees and cycles it read."""

from pathlib import Path

from django.utils import timezone

from records.models import Record, RecordSet
from records.run import run_source
from records.sources._fec_testing import FixtureFetcher

FIXTURES = Path(__file__).parent / "fixtures"
PAGES = [FIXTURES / "schedule_a-1.json", FIXTURES / "schedule_a-2.json"]


def fetcher():
    return FixtureFetcher({"/schedules/schedule_a/": PAGES})


def committee(committee_id: str, cycles: list[int]):
    rs, _ = RecordSet.objects.get_or_create(
        slug="fec-committees", defaults={"name": "FEC committees", "dataset": "fec"}
    )
    Record.objects.create(recordset=rs, key=committee_id, first_seen=timezone.now(),
                          data={"committee_id": committee_id, "cycles": cycles})  # fmt: skip


def test_run(db, sd_places):
    committee("C00367631", [2012])
    RecordSet.objects.create(slug="fec-contributions", name="FEC receipts", dataset="fec")
    f = fetcher()
    run = run_source("fec-contributions", fetcher=f)
    assert run.added == 2
    assert f.asked[0][1]["committee_id"] == "C00367631"
    assert f.asked[0][1]["two_year_transaction_period"] == 2012
    viken = Record.objects.get(key="4031620111137427306")
    assert viken.data["contributor_name"] == "VIKEN, LINDA LEA"
    assert viken.data["contribution_receipt_amount"] == 1000.0
    assert viken.data["committee_name"] == "STEPHANIE HERSETH SANDLIN FOR SOUTH DAKOTA"
    assert sorted(viken.places.values_list("short_name", flat=True)) == [
        "Rapid City", "South Dakota"]  # fmt: skip
    dunes = Record.objects.get(key="4031620111137427307")
    assert dunes.places.filter(short_name="Dakota Dunes").exists()


def test_a_run_drops_only_what_it_read(db, sd_places):
    committee("C00367631", [2012])
    rs = RecordSet.objects.create(slug="fec-contributions", name="FEC receipts", dataset="fec")
    for key, cycle in (("1", 2012), ("2", 2010), ("3", 1992)):
        Record.objects.create(recordset=rs, key=key, first_seen=timezone.now(), data={
            "committee_id": "C00367631", "two_year_transaction_period": cycle})  # fmt: skip
    Record.objects.create(recordset=rs, key="4", first_seen=timezone.now(), data={
        "committee_id": "C00000001", "two_year_transaction_period": 2012})  # fmt: skip
    run = run_source("fec-contributions", fetcher=fetcher(), force=True)
    assert (run.added, run.removed) == (2, 1)
    assert list(rs.records.dropped().values_list("key", flat=True)) == ["1"]
