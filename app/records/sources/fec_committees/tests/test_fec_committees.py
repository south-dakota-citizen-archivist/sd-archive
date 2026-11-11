"""FEC committees in South Dakota: rows from a real API response, and a run."""

from pathlib import Path

from entities.models import Mention
from records.models import Record, RecordSet
from records.run import run_source
from records.sources._fec_testing import FixtureFetcher

FIXTURES = Path(__file__).parent / "fixtures"


def test_run(db, state):
    RecordSet.objects.create(slug="fec-committees", name="FEC committees", dataset="fec")
    run = run_source("fec-committees", fetcher=FixtureFetcher({
        "/committees/": [FIXTURES / "committees.json"]}))  # fmt: skip
    assert run.added == 4
    pac = Record.objects.get(key="C00919795")
    assert (pac.data["name"], pac.data["treasurer_name"]) == (
        "605 LEADERSHIP PAC",
        "WOJCIECHOWSKI, MARIA",
    )
    roles = set(Mention.objects.filter(record_key="fec-committees:C00919795")
                .values_list("role", "name"))  # fmt: skip
    assert roles == {("committee", "605 LEADERSHIP PAC"), ("treasurer", "WOJCIECHOWSKI, MARIA")}
