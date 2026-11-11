"""State employee salaries: the parser against real search results (checked by hand), one
search against a recording of the site, and a run into the database.
Re-record when the site changes: just record app/records/sources/state_employee_salaries"""

from datetime import date
from pathlib import Path

import pytest

import sdfetch
from entities.models import Mention
from records.models import Record, RecordSet
from records.run import run_source
from records.sources import Stopped, get
from records.sources.state_employee_salaries import parse, source

FIXTURES = Path(__file__).parent / "fixtures"


def page(name: str) -> bytes:
    return (FIXTURES / f"{name}.html").read_bytes()


def test_a_search_result():
    [rhoden] = parse.rows(page("search-rhoden"))
    assert rhoden.model_dump() == {
        "active": True, "position_number": "010001", "last_name": "Rhoden",
        "first_name": "Larry", "middle_initial": "R", "agency_code": "GOV",
        "agency": "Governor's Office", "title": "Governor", "pay": "$148,522.92",
        "pay_amount": 148522.92, "pay_basis": "Annually"}  # fmt: skip


def test_former_employees_are_kept_with_their_final_wage():
    rows = parse.rows(page("search-smith"))
    assert (len(rows), sum(r.active for r in rows)) == (130, 94)
    former = next(r for r in rows if not r.active)
    assert (former.position_number, former.title, former.agency, former.pay, former.pay_basis) == (
        None,
        None,
        None,
        "$35.89",
        "Hourly",
    )
    assert len({source.key(r) for r in rows}) == 130


def test_the_form_and_its_failures():
    fields = parse.form_fields(page("search-form"))
    assert "__VIEWSTATE" in fields and "__EVENTVALIDATION" in fields
    with pytest.raises(parse.Blocked):
        parse.form_fields(BLOCK)
    with pytest.raises(parse.Blocked):  # the site's block page instead of results
        parse.rows(BLOCK)
    assert parse.rows(page("search-form")) == []  # no table: nobody by that name


class FixtureFetcher:
    """The form, and a search for each name with a fixture (none for the rest); after
    `allow` searches, the site's block page (the form without its search box)."""

    def __init__(self, store, allow=1000):
        self.store, self.allow, self.searched = store, allow, []

    def request(self, method, url, data=None, **kwargs):
        class Resp:
            content = page("search-form")

        if method == "POST":
            self.searched.append((data["tb_LastName"], data["cb_Inactive"]))
            fixture = FIXTURES / f"search-{data['tb_LastName'].casefold()}.html"
            if len(self.searched) > self.allow:
                Resp.content = BLOCK
            elif fixture.exists():
                Resp.content = fixture.read_bytes()
        return Resp()


BLOCK = page("search-form").replace(b'id="tb_LastName"', b'id="gone"')
TODAY = date(2026, 11, 10)


@pytest.fixture
def salaries(db, state):
    RecordSet.objects.create(slug="state-employee-directory", name="Directory", dataset="d")
    directory = RecordSet.objects.get(slug="state-employee-directory")
    for family in ("Rhoden", "rhoden", "Smith", "Nobody"):
        Record.objects.create(
            recordset=directory, key=family, content_hash="x",
            first_seen="2026-01-01T00:00Z", data={"family_name": family},
        )  # fmt: skip
    get("state-employee-salaries")
    return RecordSet.objects.create(slug="state-employee-salaries", name="Salaries",
                                    dataset="state-employee-salaries")  # fmt: skip


def test_run_searches_every_known_surname(salaries, state, tmp_path, monkeypatch):
    monkeypatch.setattr(source, "date", type("D", (date,), {"today": staticmethod(lambda: TODAY)}))
    assert source.surnames() == ["Nobody", "Rhoden", "Smith"]
    fetcher = FixtureFetcher(sdfetch.Store(tmp_path))
    run = run_source("state-employee-salaries", fetcher=fetcher)
    assert fetcher.searched == [("Nobody", "on"), ("Rhoden", "on"), ("Smith", "on")]
    assert (tmp_path / "searches/2026-11-10/rhoden.html").exists()
    assert (run.added, run.removed, run.checks) == (131, 0, {})
    governor = salaries.records.get(key="010001 Rhoden, Larry R")
    assert list(governor.places.all()) == [state]
    m = Mention.objects.get(record_key=f"state-employee-salaries:{governor.key}")
    assert (m.name, m.context["title"]) == ("Larry R Rhoden", "Governor")

    # the next day nothing is due, and the stored searches are read again, unchanged
    again = run_source("state-employee-salaries", fetcher=FixtureFetcher(sdfetch.Store(tmp_path)))
    assert (again.added, again.unchanged) == (0, 131)


def test_a_block_stops_the_run_and_keeps_what_it_found(salaries, tmp_path):
    fetcher = FixtureFetcher(sdfetch.Store(tmp_path), allow=2)
    run = run_source("state-employee-salaries", fetcher=fetcher)
    assert run.status == "succeeded" and run.added == 1  # Nobody (none), Rhoden (1), blocked
    assert "refusing searches after 2 searches; 1 names wait" in run.checks["stopped"]
    assert not list(tmp_path.glob("searches/*/smith.html")), "a block page isn't stored"


def test_a_blocked_form_stops_before_searching(salaries, tmp_path):
    class Blocked(FixtureFetcher):
        def request(self, method, url, data=None, **kwargs):
            class Resp:
                content = BLOCK

            return Resp()

    store = sdfetch.Store(tmp_path)
    store.save("searches/2026-11-01/rhoden.html", page("search-rhoden"))  # from a failed run
    run = run_source("state-employee-salaries", fetcher=Blocked(store))
    assert "2 names wait" in run.checks["stopped"]
    assert run.added == 1, "what's already stored is still recorded"


def test_a_days_budget(salaries, tmp_path):
    fetcher = FixtureFetcher(sdfetch.Store(tmp_path))
    with pytest.raises(Stopped, match="searched 2 names"):
        source.items(fetcher, budget=2, today=TODAY)
    assert [n for n, _ in fetcher.searched] == ["Nobody", "Rhoden"]


def test_queue_order():
    searched = {"smith": date(2026, 1, 1), "jones": date(2026, 10, 1)}
    names = ["Smith", "Jones", "Adams", "Brown"]
    assert source.queue(names, searched, TODAY) == ["Adams", "Brown", "Smith"]  # Jones is fresh


@pytest.mark.vcr
def test_the_live_site(tmp_path):
    fetcher = sdfetch.Fetcher(tmp_path, delay=0)
    fields = parse.form_fields(fetcher.request("GET", source.URL).content)
    [rhoden] = parse.rows(source.search(fetcher, fields, "Rhoden"))
    assert (rhoden.title, rhoden.agency_code) == ("Governor", "GOV")
