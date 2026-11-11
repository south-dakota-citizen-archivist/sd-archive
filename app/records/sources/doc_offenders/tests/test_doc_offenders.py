"""DOC offenders: the real results for "Q" (both pages) and four people's pages (checked by
hand: completed sentence, in prison, on parole, no facility), and runs with the search window
stood in for."""

from pathlib import Path

import pytest

from records.models import Record, RecordSet
from records.run import run_source
from records.sources.doc_offenders import parse, source
from records.sources.doc_offenders.search import NeedsPerson
from sdfetch import Store

FIXTURES = Path(__file__).parent / "fixtures"


def page(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def test_results():
    first = parse.results(page("results-q-1.html"))
    assert len(first) == 20
    assert first[0] == {"doc_number": "25287", "last_name": "Quakenbush", "first_name": "David",
                        "middle_initial": "M.", "detail_id": "kPKnHBADcW0="}  # fmt: skip
    assert parse.next_page(page("results-q-1.html"), 1) == "Page$2"
    assert parse.next_page(page("results-q-2.html"), 2) is None
    assert len(parse.results(page("results-q-2.html"))) == 20
    assert parse.detail_url("kPKnHBADcW0=") == (
        "https://docadultlookup.sd.gov/adult/lookup/details/?id=kPKnHBADcW0="
    )


def test_a_person_in_prison():
    d = parse.detail(page("person-60714.html"))
    assert d["name"] == "Quarles, Jacob L."
    assert d["fields"]["Correctional Status"] == "Active Inmate"
    assert d["fields"]["Current Facility"] == "South Dakota State Penitentiary"
    assert d["facility_address"] == ["1600 North Drive", "Sioux Falls, SD 57117"]
    assert (d["facility_city"], d["facility_state"]) == ("Sioux Falls", "SD")
    assert d["aliases"] == ["Quarles, Jacob Lee"]
    assert d["event"] == "Discharged" and d["event_date"] is None


def test_people_out():
    done = parse.detail(page("person-25287.html"))
    assert done["fields"]["Correctional Status"] == "Completed Sentence"
    assert done["event_date"] == "2010-12-16" and "facility_address" not in done
    parole = parse.detail(page("person-10959.html"))
    assert parole["fields"]["Correctional Status"] == "Active Parole"


class FakeSearcher:
    """The search window: "Q" finds the fixture's first page; every other letter nothing."""

    letters: list = []
    puzzle_at: str | None = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def search(self, letter):
        if letter == FakeSearcher.puzzle_at:
            raise NeedsPerson("a picture puzzle")
        FakeSearcher.letters.append(letter)
        return page("results-q-1.html") if letter == "Q" else b"<html><form></form></html>"


class FixtureFetcher:
    """The plain requests: page 2 of the results, and people's pages (one fixture for all)."""

    def __init__(self, store):
        self.store, self.asked = store, []

    def request(self, method, url, data=None, **kwargs):
        self.asked.append((method, url))
        if method == "POST":
            assert data["__EVENTARGUMENT"] == "Page$2"
            body = page("results-q-2.html")
        else:
            body = page("person-60714.html")
        return type("R", (), {"content": body})()


@pytest.fixture
def searcher(monkeypatch):
    FakeSearcher.letters, FakeSearcher.puzzle_at = [], None
    monkeypatch.setattr(source, "_searcher", FakeSearcher)
    return FakeSearcher


def test_a_run(db, sd_places, searcher, tmp_path):
    RecordSet.objects.create(slug="doc-offenders", name="DOC", dataset="doc-offenders")
    fetcher = FixtureFetcher(Store(tmp_path))
    run = run_source("doc-offenders", fetcher=fetcher)
    assert len(searcher.letters) == 26 and run.added == 40
    assert run.checks["by_letter"]["Q"] == 40 and run.checks["without_page"] == 0
    assert "stopped" not in run.checks
    person = Record.objects.get(key="25287")
    assert person.data["last_name"] == "Quakenbush"
    assert person.data["fields"]["Current Facility"] == "South Dakota State Penitentiary"
    assert person.places.filter(short_name="Sioux Falls").exists()
    # the same month again: nothing searched or read again
    searcher.letters = []
    fetcher.asked = []
    run = run_source("doc-offenders", fetcher=fetcher)
    assert searcher.letters == [] and fetcher.asked == [] and run.unchanged == 40


def test_a_picture_puzzle_stops_the_run_and_keeps_what_it_has(db, sd_places, searcher,
                                                              tmp_path):  # fmt: skip
    RecordSet.objects.create(slug="doc-offenders", name="DOC", dataset="doc-offenders")
    searcher.puzzle_at = "R"
    run = run_source("doc-offenders", fetcher=FixtureFetcher(Store(tmp_path)))
    assert "picture puzzle" in run.checks["stopped"]
    assert run.added == 40 and run.checks["letters_searched"] == 17  # A to Q
    assert Record.objects.filter(dropped_at__isnull=False).count() == 0


def test_a_run_reads_at_most_its_budget_of_pages(db, sd_places, searcher, tmp_path,
                                                 monkeypatch):  # fmt: skip
    from functools import partial

    RecordSet.objects.create(slug="doc-offenders", name="DOC", dataset="doc-offenders")
    monkeypatch.setattr(source.SOURCE, "items", partial(source.items, details_per_run=15))
    run = run_source("doc-offenders", fetcher=FixtureFetcher(Store(tmp_path)))
    assert run.checks["pages_read"] == 15 and run.checks["without_page"] == 25
    assert "25 left" in run.checks["stopped"]


@pytest.mark.vcr
def test_the_live_site(tmp_path):
    import sdfetch

    fetcher = sdfetch.Fetcher(tmp_path, delay=0)
    d = parse.detail(fetcher.request("GET", parse.detail_url("kPKnHBADcW0=")).content)
    assert d["fields"]["DOC Number"] == "25287" and d["name"].startswith("Quakenbush")
