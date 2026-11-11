"""Governors: the NGA's real list and pages (checked by hand), and a run."""

from pathlib import Path

from entities.models import Mention
from records.models import Document, Record, RecordSet
from records.run import run_source
from records.sources.governors import parse

FIXTURES = Path(__file__).parent / "fixtures"


def page(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def test_the_list():
    listed = parse.governors(page("former-governors.html"))
    assert len(listed) == 32
    assert listed[3] == {"url": "https://www.nga.org/governor/william-j-janklow/",
                         "listed_name": "Gov. William J. Janklow",
                         "listed_terms": ["1995 - 2003", "1979 - 1987"],
                         "listed_party": "Republican"}  # fmt: skip
    assert listed[-1]["listed_name"] == "Gov. Arthur Calvin Mellette"


def test_a_governors_page():
    d = parse.governor(page("william-j-janklow.html"))
    assert d["name"] == "William J. Janklow" and d["party"] == "Republican"
    terms = [(t["start"], t["end"]) for t in d["terms"]]
    assert terms == [("1979-01-01", "1987-01-06"), ("1995-01-07", "2003-01-07")]
    assert (d["born"], d["died"]) == ("1939-09-13", "2012-01-12")
    assert d["facts"]["Military Service"] == "Marines"
    assert d["about"].startswith("WILLIAM J. JANKLOW was born in Chicago.")
    assert d["sources"][0] == {
        "text": "South Dakota State Historical Society",
        "url": "http://www.sdhistory.org/arc/Governors/index.htm",
    }
    assert d["portrait"].endswith("/Bill_Janklow_cropped.jpg")
    assert parse.current_governor(page("william-j-janklow.html")) == (
        "https://www.nga.org/governor/larry-rhoden/"
    )
    rhoden = parse.governor(page("larry-rhoden.html"))
    assert rhoden["terms"] == [{"start": "2025-01-25", "end": None,
                                "as_published": "January 25, 2025 - Current"}]  # fmt: skip


class FixtureFetcher:
    store = None

    def request(self, method, url, **kwargs):
        if url == parse.LIST:
            body = page("former-governors.html")
        elif url.endswith("/larry-rhoden/"):
            body = page("larry-rhoden.html")
        else:
            body = page("william-j-janklow.html")  # every former governor, for the run
        return type("R", (), {"content": body})()


def test_run(db, state):
    RecordSet.objects.create(slug="governors", name="Governors", dataset="governors")
    run = run_source("governors", fetcher=FixtureFetcher())
    assert run.added == 33 and run.checks["listed"] == 32
    rhoden = Record.objects.get(key="larry-rhoden")
    assert rhoden.data["current"] is True and rhoden.data["name"] == "Larry Rhoden"
    assert Record.objects.get(key="arthur-calvin-mellette").data["current"] is False
    assert Document.objects.filter(record=rhoden, metadata__label="Portrait").count() == 1
    (m,) = Mention.objects.filter(record_key="governors:larry-rhoden")
    assert m.role == "governor" and m.original == "Larry Rhoden"
