"""IRS exempt organizations: nine real rows of the South Dakota extract (checked by hand),
and a run."""

from pathlib import Path

from entities.models import Mention
from records.models import Record, RecordSet
from records.run import run_source
from records.sources.irs_exempt_organizations import source

CSV = (Path(__file__).parent / "fixtures" / "eo_sd-sample.csv").read_bytes()


def test_abbreviated_cities():
    assert source.place("N SIOUX CITY") == source.place("North Sioux City") is not None
    assert source.place("WESSINGTN SPG") == source.place("Wessington Springs") is not None
    assert source.place("RENNER") is None  # not a census place


class FixtureFetcher:
    store = None

    def request(self, method, url, **kwargs):
        assert url == source.URL
        return type("R", (), {"content": CSV})()


def test_run(db, sd_places):
    RecordSet.objects.create(slug="irs-exempt-organizations", name="EOs",
                             dataset="irs-exempt-organizations")  # fmt: skip
    run = run_source("irs-exempt-organizations", fetcher=FixtureFetcher())
    assert run.added == 9
    church = Record.objects.get(key="010571875")
    assert church.data["name"] == "RIVER OF LIFE COMMUNITY CHURCH"
    assert church.data["in_care_of"] == "JILL FRICK" and church.data["subsection"] == "03"
    assert church.places.filter(short_name="Canton").exists()
    roles = dict(Mention.objects.filter(record_key="irs-exempt-organizations:010571875")
                 .values_list("field", "role"))  # fmt: skip
    assert roles == {"name": "organization", "in_care_of": "in care of"}
