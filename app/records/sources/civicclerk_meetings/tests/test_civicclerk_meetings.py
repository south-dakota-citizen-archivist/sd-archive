"""CivicClerk meetings: three of Spearfish's real events (an old one with no files, two with
agenda, packet, minutes and video; checked by hand), served as two pages, and a run."""

import json
from pathlib import Path

import pytest

from records.models import Document, Record, RecordSet
from records.run import run_source
from records.sources import _civicclerk as C
from records.sources import _governments

EVENTS = json.loads((Path(__file__).parent / "fixtures" / "events.json").read_text())["value"]
NEXT = "https://spearfishsd.api.civicclerk.com/v1/Events?$skiptoken=x"


class FixtureFetcher:
    store = None

    def __init__(self):
        self.asked = []

    def request(self, method, url, params=None, **kwargs):
        self.asked.append(url)
        if url == f"{C.api('spearfishsd')}/Events":
            body = {"value": EVENTS[:2], "@odata.nextLink": NEXT}
        else:
            assert url == NEXT and params is None
            body = {"value": EVENTS[2:]}
        return type("R", (), {"json": lambda self: body})()


def test_events_follow_next_links():
    fetcher = FixtureFetcher()
    assert [e["id"] for e in C.events(fetcher, "spearfishsd")] == [1817, 2196, 3404]
    assert len(fetcher.asked) == 2


def test_a_meeting():
    m = C.meeting(EVENTS[1], "spearfishsd")
    assert (m["eventName"], m["categoryName"]) == ("City Council Study Session", "City Council")
    assert m["location"] == "City Hall, 625 N 5th St, Spearfish, SD, 57783"
    assert m["video"] == "https://cpmedia.azureedge.net/spearfishsd/c414b7ca85.mp4"
    assert [(f["type"], f["file_id"]) for f in m["files"]] == [
        ("Agenda", 8925), ("Agenda Packet", 8927), ("Minutes", 8945)]  # fmt: skip
    assert m["files"][1]["portal_url"] == (
        "https://spearfishsd.portal.civicclerk.com/event/2196/files/agenda-packet/8927"
    )
    assert C.meeting(EVENTS[0], "spearfishsd")["files"] == []


@pytest.fixture
def spearfish_only(monkeypatch):
    only = [g for g in _governments.GOVERNMENTS if g["name"] == "City of Spearfish"]
    monkeypatch.setattr(_governments, "GOVERNMENTS", only)


def test_run(db, sd_places, spearfish_only):
    RecordSet.objects.create(slug="civicclerk-meetings", name="Meetings",
                             dataset="local-government-meetings")  # fmt: skip
    run = run_source("civicclerk-meetings", fetcher=FixtureFetcher())
    assert run.added == 3
    assert run.checks["governments"]["City of Spearfish"] == {"events": 3, "with_files": 2}
    meeting = Record.objects.get(key="spearfishsd:2196")
    assert meeting.source_url == "https://spearfishsd.portal.civicclerk.com/event/2196"
    files = Document.objects.filter(record=meeting).order_by("url")
    assert files.count() == 3 and files[0].url == C.file_url("spearfishsd", 8925)
    assert meeting.places.filter(short_name="Spearfish").exists()
