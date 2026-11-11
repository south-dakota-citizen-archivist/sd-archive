"""Cemetery burials (BurialSearch): the real directory entries, Rose Hill's page (the parts
the map uses), map features and burial records (checked by hand), and a run."""

import json
from pathlib import Path

import pytest

from entities.models import Mention
from records.models import Record, RecordSet
from records.run import run_source
from records.sources import _burialsearch as B
from records.sources.cemetery_burials import source
from sdfetch import Store

FIXTURES = Path(__file__).parent / "fixtures"


def text(name: str) -> str:
    return (FIXTURES / name).read_text()


def test_unwrap():
    assert B.unwrap([{"a": [[1, 2], {"s": "arr"}]}, {"s": "arr"}]) == {"a": [1, 2]}
    assert B.unwrap([[[{"x": 1}, {"s": "arr"}]], {"s": "arr"}]) == [{"x": 1}]


def test_directory_and_settings():
    listed = B.directory(text("directory.html"))
    assert [(c["name"], c["address"], c["url"]) for c in listed] == [
        ("Rose Hill Cemetery", "Spearfish, SD", "https://burialsearch.com/797"),
        ("City of Cheyenne Cemetery", "Cheyenne, WY", "https://burialsearch.com/519"),
    ]  # fmt: skip
    s = B.settings(text("rose-hill-page.html"))
    assert s["user_id"] == 120 and s["schemas"] == ["schema_797"]
    assert s["schema_group"]["schema_797"]["product"] == "Platinum"
    assert s["cemetery"]["name"] == "Rose Hill Cemetery - Spearfish, SD"
    box = B.bounds(s["schema_group"])
    assert box["nwLat"] < -103.853978513 and box["seLng"] < 44.477663025  # padded outward


class FakeSession:
    def __init__(self, fetcher, url):
        assert url == "https://burialsearch.com/797"
        self.url, self.asked = url, []

    def layers(self):
        return json.loads(text("rose-hill-layers.json"))

    def burials_in(self, sid, schema):
        FakeSession.asked.append(sid)
        return json.loads(text(f"details-{sid}.json"))


class FixtureFetcher:
    def __init__(self, store):
        self.store = store

    def request(self, method, url, **kwargs):
        assert url == B.BASE_URL + "/"
        return type("R", (), {"text": text("directory.html")})()


@pytest.fixture
def fake(monkeypatch):
    FakeSession.asked = []
    monkeypatch.setattr(B, "Session", FakeSession)
    return FakeSession


def test_run(db, sd_places, fake, tmp_path):
    RecordSet.objects.create(slug="cemetery-burials", name="Burials", dataset="cemetery-burials")
    fetcher = FixtureFetcher(Store(tmp_path))
    run = run_source("cemetery-burials", fetcher=fetcher)
    assert run.added == 2 and run.checks["without_record"] == 0  # not Cheyenne's
    assert run.checks["cemeteries"]["Rose Hill Cemetery"] == {"burials": 2, "with_record": 2,
                                                              "spaces": 4}  # fmt: skip
    mcphee = Record.objects.get(key="schema_797:51")
    assert mcphee.data["record"]["Location"] == "Block A, Section 11, Lot 10, Space 3"
    assert (mcphee.data["birth_date"], mcphee.data["death_date"]) == ("1856-12-22", "1896-08-09")
    assert mcphee.geometry is not None and mcphee.places.filter(short_name="Spearfish").exists()
    roles = list(Mention.objects.filter(record_key="cemetery-burials:schema_797:51")
                 .order_by("position").values_list("role", "original"))  # fmt: skip
    assert roles == [
        ("interred", "Elizabeth McPhee"),
        ("interred (maiden name)", "Elizabeth Currie"),
    ]  # "Family McPhee" isn't one
    # again this month: the records are kept, none asked for again
    run = run_source("cemetery-burials", fetcher=fetcher)
    assert run.unchanged == 2 and sorted(fake.asked) == ["1583", "1587"]


def test_a_run_stops_at_its_budget(db, sd_places, fake, tmp_path, monkeypatch):
    from functools import partial

    RecordSet.objects.create(slug="cemetery-burials", name="Burials", dataset="cemetery-burials")
    monkeypatch.setattr(source.SOURCE, "items", partial(source.items, details_per_run=1))
    run = run_source("cemetery-burials", fetcher=FixtureFetcher(Store(tmp_path)))
    assert run.added == 2 and run.checks["without_record"] == 1 and "stopped" in run.checks
