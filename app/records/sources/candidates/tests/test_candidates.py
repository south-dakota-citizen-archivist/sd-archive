"""Candidates: real candidate list pages (the 2026 general's first page, a 2023 special
election), read and run; places for each kind of contest; the live site against a recording.
Re-record when the site changes: just record app/records/sources/candidates"""

from pathlib import Path

import pytest

import sdfetch
from entities.models import Mention
from records.models import Record
from records.run import run_source
from records.sources.candidates import source

FIXTURES = Path(__file__).parent / "fixtures"
ONE_PAGE = (b"<strong>745</strong> items in <strong>15</strong> pages",
            b"<strong>50</strong> items in <strong>1</strong> pages")  # fmt: skip


class FixtureFetcher:
    store = None

    def request(self, method, url, **kwargs):
        eid = url.rsplit("=", 1)[1]

        class Resp:
            content = (FIXTURES / "774-page-1.html").read_bytes().replace(*ONE_PAGE) if (
                eid == "774") else (FIXTURES / "580.html").read_bytes()  # fmt: skip

        return Resp()


ELECTIONS = [{"eid": "774", "title": "General Election", "date": "2026-11-03"},
             {"eid": "580", "title": "Special Election", "date": "2023-06-06"}]  # fmt: skip


def test_rows():
    rows = source.rows(FixtureFetcher(), "774", "2026-11")
    assert len(rows) == 50
    rounds = next(r for r in rows if r["name"] == "Mike Rounds")
    assert {k: rounds[k] for k in ("contest", "party", "filed", "ballot_order", "status",
                                   "election_date", "city", "district_type")} == {
        "contest": "United States Senator", "party": "REP", "filed": "2026-03-27",
        "ballot_order": "1", "status": "Active", "election_date": "2026-11-03",
        "city": "Fort Pierre", "district_type": "SW"}  # fmt: skip
    withdrawn = next(r for r in rows if r["status"] == "Withdrawn")
    assert withdrawn["withdrawn"] == "2026-08-04"


@pytest.mark.parametrize(
    ("kind", "district", "place"),
    [
        ("SW", "", "0400000US46"),
        ("SEN", "District 01", "610U900US46001"),
        ("HOU", "District 26A", "620L900US4626A"),
        ("CTY", "Davison", "0500000US46035"),
        ("COM", "Lyman - District 3-4-5", "0500000US46085"),
        ("PRE", "Aurora - Precinct-2", "0500000US46003"),
        ("SCH", "Sioux Falls School District 49-5", "9700000US4666270"),
        ("WAR", "Philip Ward-2", "1600000US4649300"),
        ("WDD", "East Dakota WDD 1", None),
        ("WAR", "Ward-1", None),
    ],
)
def test_contest_places(kind, district, place):
    assert source.contest_place(kind, district) == place


def test_run(db, state, sd_all_places, monkeypatch):
    monkeypatch.setattr(source, "elections", lambda: ELECTIONS)
    run = run_source("candidates", fetcher=FixtureFetcher())
    special = Record.objects.filter(data__eid=580).count()
    assert run.added == 50 + special and special > 0
    assert run.checks["elections_with_lists"] == 2
    rounds = Record.objects.get(key="774 United States Senator |  | Mike Rounds")
    assert {p.geoidfq for p in rounds.places.all()} >= {"0400000US46"}
    ticket = Record.objects.filter(data__contest="Governor and Lieutenant Governor").first()
    names = set(Mention.objects.filter(record_key=f"candidates:{ticket.key}")
                .values_list("name", flat=True))  # fmt: skip
    assert len(names) == 2, "a governor and lieutenant governor ticket names both"


@pytest.mark.vcr
def test_the_live_site(tmp_path):
    rows = source.rows(sdfetch.Fetcher(tmp_path, delay=0), "580", "2026-11")
    assert rows and all(r["contest"] for r in rows)
