"""FEC candidates from South Dakota: rows from a real API response, and a run."""

from pathlib import Path

from entities.models import Mention
from records.models import Record, RecordSet
from records.run import run_source
from records.sources._fec_testing import FixtureFetcher

FIXTURES = Path(__file__).parent / "fixtures"


def test_run(db, state, settings):
    settings.FEC_API_KEY = "test-key"
    RecordSet.objects.create(slug="fec-candidates", name="FEC candidates", dataset="fec")
    fetcher = FixtureFetcher({"/candidates/": [FIXTURES / "candidates.json"]})
    run = run_source("fec-candidates", fetcher=fetcher)
    assert run.added == 4
    params = fetcher.asked[0][1]
    assert params.pop("api_key") == "test-key"
    assert params == {"state": "SD", "sort": "name", "page": 1, "per_page": 100}
    abdnor = Record.objects.get(key="S0SD00054")
    assert (abdnor.data["name"], abdnor.data["office"]) == ("ABDNOR, JAMES", "S")
    assert abdnor.source_url == "https://www.fec.gov/data/candidate/S0SD00054/"
    m = Mention.objects.get(record_key="fec-candidates:S0SD00054")
    assert (m.name, m.role, m.properties["lastName"]) == ("ABDNOR, JAMES", "candidate", ["ABDNOR"])
