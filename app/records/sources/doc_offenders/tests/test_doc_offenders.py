"""DOC offenders: the real results for "Q" (all five pages) and four people's pages (checked by
hand: completed sentence, in prison, on parole, no facility), and runs with the search window
stood in for."""

import io
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
    # the first page's pager quotes plainly, later pages' as &#39;
    assert parse.next_page(page("results-q-1.html"), 1) == "Page$2"
    assert parse.next_page(page("results-q-2.html"), 2) == "Page$3"
    assert parse.next_page(page("results-q-5.html"), 5) is None
    assert len(parse.results(page("results-q-5.html"))) == 17
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
    unavailable_at: set = set()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def search(self, letter):
        if letter == FakeSearcher.puzzle_at:
            raise NeedsPerson("a picture puzzle")
        FakeSearcher.letters.append(letter)
        if letter in FakeSearcher.unavailable_at:
            return UNAVAILABLE
        return page("results-q-1.html") if letter == "Q" else NO_ONE


NO_ONE = b'<html><form id="frmInmate"><table id="grvList"></table></form></html>'
UNAVAILABLE = (b'<html><form id="frmInmate"><div id="pnlError">The Offender Locator is currently '
               b"unavailable. Please check back at a later time.</div></form></html>")  # fmt: skip


class FixtureFetcher:
    """The plain requests: Q's later result pages, and people's pages (one fixture for all)."""

    def __init__(self, store):
        self.store, self.asked = store, []

    def request(self, method, url, data=None, **kwargs):
        self.asked.append((method, url))
        if method == "POST":
            n = data["__EVENTARGUMENT"].removeprefix("Page$")
            body = page(f"results-q-{n}.html")
        else:
            body = page("person-60714.html")
        return type("R", (), {"content": body})()


@pytest.fixture
def searcher(monkeypatch):
    FakeSearcher.letters, FakeSearcher.puzzle_at, FakeSearcher.unavailable_at = [], None, set()
    monkeypatch.setattr(source, "_searcher", FakeSearcher)
    for pace in ("SEARCH_GAP", "PAGE_GAP", "DETAIL_GAP", "BREAK"):
        monkeypatch.setattr(source, pace, (0.0, 0.0))
    return FakeSearcher


def test_a_run(db, sd_places, searcher, tmp_path):
    RecordSet.objects.create(slug="doc-offenders", name="DOC", dataset="doc-offenders")
    fetcher = FixtureFetcher(Store(tmp_path))
    run = run_source("doc-offenders", fetcher=fetcher)
    assert len(searcher.letters) == 26 and run.added == 97
    assert run.checks["by_letter"]["Q"] == 97 and run.checks["without_page"] == 0
    assert "stopped" not in run.checks
    person = Record.objects.get(key="25287")
    assert person.data["last_name"] == "Quakenbush"
    assert person.data["fields"]["Current Facility"] == "South Dakota State Penitentiary"
    assert person.places.filter(short_name="Sioux Falls").exists()
    from entities.models import Mention

    names = Mention.objects.filter(record_key="doc-offenders:25287").order_by("position")
    assert [(m.field, m.original, m.role) for m in names] == [
        ("name", "Quakenbush, David M.", "offender"),
        ("aliases", "Quarles, Jacob Lee", "offender"),  # the fixture page is someone else's
    ]  # fmt: skip
    # rebuilt from the records, the same
    from django.core.management import call_command

    Mention.objects.filter(dataset="doc-offenders").delete()
    call_command("rebuild_mentions", "doc-offenders", stdout=io.StringIO())
    assert Mention.objects.filter(record_key="doc-offenders:25287").count() == 2
    # the same month again: nothing searched or read again
    searcher.letters = []
    fetcher.asked = []
    run = run_source("doc-offenders", fetcher=fetcher)
    assert searcher.letters == [] and fetcher.asked == [] and run.unchanged == 97


def test_a_picture_puzzle_stops_the_run_and_keeps_what_it_has(db, sd_places, searcher,
                                                              tmp_path):  # fmt: skip
    RecordSet.objects.create(slug="doc-offenders", name="DOC", dataset="doc-offenders")
    searcher.puzzle_at = "R"
    run = run_source("doc-offenders", fetcher=FixtureFetcher(Store(tmp_path)))
    assert "picture puzzle" in run.checks["stopped"]
    assert run.added == 97 and run.checks["letters_searched"] == 17  # A to Q
    assert Record.objects.filter(dropped_at__isnull=False).count() == 0


def test_a_letter_too_big_for_the_locator_is_searched_two_letters_at_a_time(
        db, sd_places, searcher, tmp_path, monkeypatch):  # fmt: skip
    """The locator answers a search for H "currently unavailable" (too many people): H is
    searched as HA to HZ and H', and in later months it goes straight to those."""
    from datetime import date
    from functools import partial

    RecordSet.objects.create(slug="doc-offenders", name="DOC", dataset="doc-offenders")
    searcher.unavailable_at = {"H"}
    store = Store(tmp_path)
    monkeypatch.setattr(source.SOURCE, "items", partial(source.items, today=date(2026, 11, 1)))
    run = run_source("doc-offenders", fetcher=FixtureFetcher(store))
    assert "stopped" not in run.checks and run.checks["split"] == ["H"]
    hs = [x for x in searcher.letters if x.startswith("H")]
    assert hs == ["H", "HA", "HB", *[f"H{c}" for c in "CDEFGHIJKLMNOPQRSTUVWXYZ'"]]
    assert searcher.letters[searcher.letters.index("H'") + 1] == "I"  # then on to I
    assert run.checks["letters_searched"] == 26 and run.checks["searches"] == 26 + 26
    assert store.has("results/2026-11/H/split") and not store.has("results/2026-11/H/complete")
    # next month: no search for H itself
    searcher.letters = []
    monkeypatch.setattr(source.SOURCE, "items", partial(source.items, today=date(2026, 12, 1)))
    run_source("doc-offenders", fetcher=FixtureFetcher(store))
    assert "H" not in searcher.letters and "HA" in searcher.letters


def test_a_two_letter_search_the_locator_cant_answer_stops_the_run(db, sd_places, searcher,
                                                                    tmp_path):  # fmt: skip
    """The locator unavailable for a two-letter search too: it may be down; stop, store
    nothing of that search, and search it again next run."""
    RecordSet.objects.create(slug="doc-offenders", name="DOC", dataset="doc-offenders")
    searcher.unavailable_at = {"H", "HE"}
    store = Store(tmp_path)
    run = run_source("doc-offenders", fetcher=FixtureFetcher(store))
    assert "'HE'" in run.checks["stopped"] and "currently unavailable" in run.checks["stopped"]
    month = f"{run.started_at:%Y-%m}"
    assert store.has(f"results/{month}/HA/complete")
    assert not store.has(f"results/{month}/HE/complete")
    assert Record.objects.filter(dropped_at__isnull=False).count() == 0
    # the next run picks up at HE
    searcher.letters, searcher.unavailable_at = [], {"H"}
    run = run_source("doc-offenders", fetcher=FixtureFetcher(store))
    assert searcher.letters[0] == "HE" and "stopped" not in run.checks


def test_searches_are_spaced_and_pages_read_in_between(db, sd_places, searcher, tmp_path,
                                                       monkeypatch):  # fmt: skip
    """With searches 10 "seconds" apart and each page read taking 1, the reading happens
    while the next search waits."""
    from functools import partial

    now = [0.0]
    searched_at = []
    original = FakeSearcher.search

    def search(self, letter):
        searched_at.append(now[0])
        return original(self, letter)

    class Clocked(FixtureFetcher):
        def request(self, method, url, **kwargs):
            if method == "GET":
                now[0] += 1
            return super().request(method, url, **kwargs)

    def sleep(seconds):
        now[0] += seconds

    monkeypatch.setattr(FakeSearcher, "search", search)
    RecordSet.objects.create(slug="doc-offenders", name="DOC", dataset="doc-offenders")
    items = partial(source.items, search_gap=(10.0, 10.0), clock=lambda: now[0], sleep=sleep)
    monkeypatch.setattr(source.SOURCE, "items", items)
    run_source("doc-offenders", fetcher=Clocked(Store(tmp_path)))
    gaps = [b - a for a, b in zip(searched_at, searched_at[1:], strict=False)]
    assert len(searched_at) == 26 and min(gaps) >= 10
    assert searched_at[17] - searched_at[16] == 10  # R waited while 10 of Q's pages were read


def test_a_run_reads_at_most_its_budget_of_pages(db, sd_places, searcher, tmp_path,
                                                 monkeypatch):  # fmt: skip
    from functools import partial

    RecordSet.objects.create(slug="doc-offenders", name="DOC", dataset="doc-offenders")
    monkeypatch.setattr(source.SOURCE, "items", partial(source.items, details_per_run=15))
    run = run_source("doc-offenders", fetcher=FixtureFetcher(Store(tmp_path)))
    assert run.checks["pages_read"] == 15 and run.checks["without_page"] == 82
    assert "82 left" in run.checks["stopped"]


def test_the_locator_unavailable_for_peoples_pages_waits_then_stops(
        db, sd_places, searcher, tmp_path, monkeypatch):  # fmt: skip
    """A person's page answered "currently unavailable": tried again after each cool-down;
    still unavailable, the run stops (partial, nothing dropped) and reads it next run."""
    RecordSet.objects.create(slug="doc-offenders", name="DOC", dataset="doc-offenders")
    monkeypatch.setattr(source, "COOL_DOWN", (0.0, 0.0))

    class Down(FixtureFetcher):
        down_after = 5  # people's pages read before the locator goes down

        def request(self, method, url, data=None, **kwargs):
            if method == "GET" and sum(m == "GET" for m, _ in self.asked) >= self.down_after:
                self.asked.append((method, url))
                return type("R", (), {"content": UNAVAILABLE})()
            return super().request(method, url, data, **kwargs)

    store = Store(tmp_path)
    run = run_source("doc-offenders", fetcher=Down(store))
    assert "currently unavailable" in run.checks["stopped"]
    assert run.checks["pages_read"] == 5 and run.checks["without_page"] == 92
    assert Record.objects.filter(dropped_at__isnull=False).count() == 0
    # back up: the next run reads the rest (the one that was unavailable too)
    run = run_source("doc-offenders", fetcher=FixtureFetcher(store))
    assert "stopped" not in run.checks and run.checks["without_page"] == 0


@pytest.mark.vcr
def test_the_live_site(tmp_path):
    import sdfetch

    fetcher = sdfetch.Fetcher(tmp_path, delay=0)
    d = parse.detail(fetcher.request("GET", parse.detail_url("kPKnHBADcW0=")).content)
    assert d["fields"]["DOC Number"] == "25287" and d["name"].startswith("Quakenbush")


def test_peoples_pages_are_read_at_a_persons_pace(tmp_path, monkeypatch):
    """A gap before every page after the first, and a break every few pages."""
    monkeypatch.setattr(source, "DETAIL_GAP", (5.0, 5.0))
    monkeypatch.setattr(source, "BREAK_EVERY", (3, 3))
    monkeypatch.setattr(source, "BREAK", (300.0, 300.0))
    slept = []
    rows = parse.results(page("results-q-1.html"))[:7]
    people = source._People(FixtureFetcher(Store(tmp_path)), Store(tmp_path), "2026-11", 100,
                            sleep=slept.append)  # fmt: skip
    people.add(rows)
    while people.read_one():
        pass
    assert people.read == 7
    # 6 gaps (none before the first page), and a break after every 3rd gap
    assert slept == [5.0, 5.0, 5.0, 300.0, 5.0, 5.0, 5.0, 300.0]
