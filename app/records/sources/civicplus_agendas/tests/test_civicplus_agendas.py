"""CivicPlus Agenda Center: two of Lawrence County's boards from its real Agenda Center page,
and the County Commission's 2016 list (checked by hand), and a run."""

from pathlib import Path

import pytest

from records.models import Document, Record, RecordSet
from records.run import run_source
from records.sources import _governments
from records.sources._civicplus import agendas

FIXTURES = Path(__file__).parent / "fixtures"
BASE = "https://www.lawrence.sd.us"
HOME = (FIXTURES / "lawrence-agenda-center.html").read_text()
Y2016 = (FIXTURES / "lawrence-commission-2016.html").read_text()


def test_boards_years_and_meetings():
    assert agendas.categories(HOME) == {"2": "County Commissioner & Board of Adjustment",
                                        "4": "Fair Board"}  # fmt: skip
    assert agendas.years(HOME, "2")[:2] == [2026, 2025] and agendas.years(HOME, "2")[-1] == 2015
    current = agendas.meetings(HOME, BASE)[0]
    assert current["agenda_id"] == "1360" and current["date"] == "2026-11-10"
    assert current["files"]["packet"].endswith("_11102026-1360?packet=true")
    assert current["media"] == ["https://www.youtube.com/watch?v=TAR7i5Gd8GA"]
    old = agendas.meetings(Y2016, BASE)
    assert len(old) == 25
    assert old[0] == {"agenda_id": "381", "date": "2016-12-27",
                      "title": "County Commission Meeting Agenda (PDF)", "posted": None,
                      "files": {"agenda": f"{BASE}/AgendaCenter/ViewFile/Agenda/_12272016-381",
                                "minutes": f"{BASE}/AgendaCenter/ViewFile/Minutes/_12272016-381"},
                      "media": []}  # fmt: skip


class FixtureFetcher:
    """Lawrence County's Agenda Center: every year list is 2016's (for the run's sake)."""

    store = None

    def __init__(self):
        self.posts = []

    def request(self, method, url, data=None, **kwargs):
        if method == "GET":
            assert url == f"{BASE}/AgendaCenter"
            return type("R", (), {"content": HOME.encode()})()
        self.posts.append((data["catID"], data["year"]))
        body = Y2016 if (data["catID"], data["year"]) == ("2", "2016") else "<table></table>"
        return type("R", (), {"content": body.encode()})()


@pytest.fixture
def lawrence_only(monkeypatch):
    only = [g for g in _governments.GOVERNMENTS if g["name"] == "Lawrence County"]
    monkeypatch.setattr(_governments, "GOVERNMENTS", only)


def test_run(db, sd_counties, lawrence_only):
    RecordSet.objects.create(slug="civicplus-agendas", name="Agendas",
                             dataset="local-government-meetings")  # fmt: skip
    fetcher = FixtureFetcher()
    run = run_source("civicplus-agendas", fetcher=fetcher)
    every_year = len(agendas.years(HOME, "2")) + len(agendas.years(HOME, "4"))
    assert run.added == 25 and len(fetcher.posts) == every_year  # each board's every year
    meeting = Record.objects.get(key="www.lawrence.sd.us:381")
    assert meeting.data["board"] == "County Commissioner & Board of Adjustment"
    assert meeting.data["government"] == "Lawrence County"
    assert sorted(Document.objects.filter(record=meeting).values_list("metadata__label",
                                                                      flat=True)) == [
        "Agenda", "Minutes"]  # fmt: skip
    assert meeting.places.filter(geoid="46081").exists()
