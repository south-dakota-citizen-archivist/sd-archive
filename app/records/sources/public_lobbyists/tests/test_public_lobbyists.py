"""Public lobbyists: the printer-friendly PDF's first page (real, checked by hand) read, and
a run including the registrations the site lists as private."""

from pathlib import Path

from records.models import Record, RecordSet
from records.run import run_source
from records.sources import _lobbyists as L
from records.sources.public_lobbyists import source

FIXTURES = Path(__file__).parent / "fixtures"
PDF = (FIXTURES / "printable-page-1.pdf").read_bytes()


def test_the_printable_list():
    rows = L.pdf_rows(PDF, "public")
    assert len(rows) == 9
    assert rows[0] == {
        "year": "2012", "lobbyist_name": "CHARLES D. MCGUIGAN", "lobbyist_name_address": "",
        "agency": "ATTORNEY GENERAL",
        "agency_address": "1302 EAST HIGHWAY 14, SUITE 1 PIERRE, SD 57501",
        "agency_address_street": "1302 EAST HIGHWAY 14, SUITE 1",
        "agency_address_city": "PIERRE", "agency_address_state": "SD",
        "agency_address_zip": "57501", "agency_address_country": None,
    }  # fmt: skip


class FixtureSession:
    def __init__(self, fetcher):
        self.chosen = None

    def choose(self, kind):
        self.chosen = kind

    def search(self, **kwargs):
        assert self.chosen == "public"

    def printable(self):
        return PDF


def test_run(db, sd_places, monkeypatch):
    monkeypatch.setattr(L, "Session", FixtureSession)
    RecordSet.objects.create(slug="public-lobbyists", name="Public lobbyists", dataset="lobbyists")
    run = run_source("public-lobbyists", fetcher=type("F", (), {"store": None})())
    assert run.added == 9 + len(L.FIXES["public_but_private"])
    record = Record.objects.get(key="2012 CHARLES D. MCGUIGAN | ATTORNEY GENERAL")
    assert sorted(record.places.values_list("short_name", flat=True)) == ["Pierre", "South Dakota"]
    moved = Record.objects.get(data__listed_as="private", data__lobbyist_name="TIFFANY SANDERSON")
    assert moved.data["agency"] == "LAKE AREA TECHNICAL COLLEGE"
    assert moved.data["agency_address_city"] == "WATERTOWN"
    assert moved.places.filter(short_name="Watertown").exists()


def test_keys_keep_each_agency():
    a = {
        "year": 2013,
        "lobbyist_name": "CAROL TELLINGHUISEN",
        "agency": "BOARD OF FUNERAL SERVICES",
    }
    b = {**a, "agency": "BOARD OF HEARING AID DISPENSERS"}
    assert source.key(a) != source.key(b)
