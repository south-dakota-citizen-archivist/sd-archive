"""Polling places: real pages cut from the Secretary of State's lists, one or more for each
layout, with the values checked by hand against the printed PDFs; a run against them.

- 2016 general (page 1): no precinct; State and Vote Center columns.
- 2018 general (pages 1, 29): Vote Center column; Hyde's wrapped "Memorial Auditorium -".
- 2024 primary (pages 1, 26): a centered name wider than its cell ("Bill Conquering Bear
  Gymnasium/…" starts left of its column); Oglala Lakota's eight vote centers per precinct.
- 2026 primary (pages 1, 9, 23): clipped second lines drawn inside the next row ("Belle" /
  "Fourche"); a line spilling under the table ("Council Chambers"); a merged header cell.
- 2026 runoff (pages 1, 15, 16, 26): no visible rules, read by position; a name wrapped
  over a page break ("WAVERLY COMMUNITY" / "CENTER").
- 2026 general (page 1): five columns, no precinct.
- Absentee satellite sites, 2018 and 2024 (whole files).
"""

from datetime import date
from pathlib import Path

import pytest

from records.sources import _sos_search
from records.sources.polling_places import parse, source

FIXTURES = Path(__file__).parent / "fixtures"


def listing(name: str) -> parse.Listing:
    return parse.read((FIXTURES / name).read_bytes())


def row(found, **match) -> dict:
    (one,) = [r for r in found.rows if all(r.get(k) == v for k, v in match.items())]
    return one


def test_2016_general():
    found = listing("2016-general-p1.pdf")
    assert len(found.rows) == 53 and found.title.startswith("2016 General Election")
    assert found.rows[0] == {"county": "Aurora", "polling_place": "Aurora County Ag Building",
                             "address": "1302 E 1st St", "city": "Plankinton", "state": "SD",
                             "instructions": "Use West Door", "vote_center": "",
                             "page": 1}  # fmt: skip


def test_2018_general_wrapped_names_and_vote_centers():
    found = listing("2018-general-p1-29.pdf")
    assert found.updated == "10/25/2018" and len(found.rows) == 56
    hyde = row(found, county="Hyde", precinct="Precinct-1")
    assert hyde["polling_place"] == "Hyde County Memorial Auditorium - Main Arena"
    assert hyde["vote_center"] == "Vote Center"


def test_2024_primary_a_name_wider_than_its_cell():
    found = listing("2024-primary-p1-26.pdf")
    batesland = [r for r in found.rows if r["precinct"] == "Batesland"]
    assert len(batesland) == 8  # any of the county's eight vote centers
    assert batesland[0]["polling_place"] == (
        "Bill Conquering Bear Gymnasium/Oglala Lakota County Schools")  # fmt: skip
    assert batesland[0]["address"] == "409 3rd St"  # not "ls 409 3rd St"


def test_2026_primary_clipped_lines_and_a_spill():
    found = listing("2026-primary-p1-9-23.pdf")
    assert len(found.rows) == 109
    for precinct in ("Precinct-16", "Precinct-17"):  # read by position: "Belle", "FBoeullerc he"
        assert row(found, county="Butte", precinct=precinct)["city"] == "Belle Fourche"
    brandon = row(found, precinct="Precinct-VP05")
    assert brandon["polling_place"] == "Brandon City Hall - Council Chambers"
    assert brandon["page"] == 3


def test_2026_runoff_read_by_position():
    found = listing("2026-runoff-p1-15-16-26.pdf")
    assert len(found.rows) == 98 and not found.unread
    assert found.rows[0] == {"county": "Aurora", "precinct": "Precinct-1",
                             "polling_place": "Stickney Community Building",
                             "address": "400 Main St", "city": "Stickney",
                             "instructions": "Use Front Door", "page": 1}  # fmt: skip
    waverly = row(found, precinct="WAVERLY")
    assert waverly["polling_place"] == "WAVERLY COMMUNITY CENTER"  # "CENTER" tops page 16
    assert waverly["carried_to_page"] == 3
    assert row(found, county="Lincoln", precinct="27")["polling_place"] == "Heritage Center"


def test_2026_general():
    found = listing("2026-general-p1.pdf")
    assert found.updated == "10.27.26" and len(found.rows) == 51
    assert set(found.rows[0]) == {"county", "polling_place", "address", "city", "instructions",
                                  "page"}  # fmt: skip


def test_an_unknown_column_stops_the_list():
    with pytest.raises(parse.LayoutChanged):
        parse.fields_for(["County", "Precinct", "Ward", "Address", "City"])
    assert not parse.is_header("County Building")  # a wrapped name, not the header


def test_satellite_sites():
    sites = parse.satellite_sites((FIXTURES / "2018-absentee-satellite-sites.pdf").read_bytes())
    assert len(sites.rows) == 8  # the totals line isn't a site
    wall = sites.rows[5]["printed"]
    assert wall["Building"] == "Wall City Finance Office" and wall["City/Town"] == "Wall"
    sites = parse.satellite_sites((FIXTURES / "2024-absentee-satellite-sites.pdf").read_bytes())
    assert [r["printed"]["County"] for r in sites.rows][-3:] == [
        "Pennington - Hill City", "Pennington - Wall", "Todd"]  # fmt: skip


FILES = {
    "2018-11-06-general": "2018-general-p1-29.pdf",
    "2018-absentee-satellite-sites": "2018-absentee-satellite-sites.pdf",
    "2026-06-02-primary": "2026-primary-p1-9-23.pdf",
    "2026-07-28-runoff": "2026-runoff-p1-15-16-26.pdf",
}


class FixtureFetcher:
    """Lists from the fixtures (others fail to fetch); the 2026 polling place page, and the
    site's 404 page for every other year."""

    store = None

    def request(self, method, url, **kwargs):
        if url == source.UPCOMING_PAGE.format(year=2026):
            body = (FIXTURES / "2026-polling-places-page.html").read_bytes()
        elif url.endswith(".aspx"):
            body = b"<html><head><title>South Dakota Secretary of State</title></head></html>"
        else:
            plist = source.known(url)
            name = FILES.get(plist.slug) if plist else None
            if name is None:
                raise OSError(f"404 {url}")
            body = (FIXTURES / name).read_bytes()
        return type("Response", (), {"content": body})()


@pytest.fixture
def no_pause(monkeypatch):
    monkeypatch.setattr(_sos_search, "_pace", lambda *a, **k: None)


def test_a_run(no_pause):
    found = source.items(FixtureFetcher(), today=date(2026, 11, 16))
    assert found.checks["lists"] == {
        "2018-11-06-general": 56, "2018-absentee-satellite-sites": 8,
        "2026-06-02-primary": 109, "2026-07-28-runoff": 98}  # fmt: skip
    assert "2026-11-03-general" in found.checks["failed"]  # not in the fixtures: its records stay
    assert "lists_not_in_the_table" not in found.checks  # the page's three 2026 lists are known
    assert found.checks["pages_read"] == 1
    belle = next(i for i in found if i.data["city"] == "Belle Fourche")
    assert belle.key == ("2026-06-02 Primary Election | Butte | Precinct-11 | "
                         "Belle Fourche Community Hall")  # fmt: skip
    # the state, Butte County, Belle Fourche city
    assert belle.places == ["0400000US46", "0500000US46019", "1600000US4604380"]
    assert belle.data["eid"] == 773 and belle.data["vote_center"] is None
    assert belle.source_url.endswith("2026%20Primary%20Polling%20Places.pdf#page=2")
    hyde = next(i for i in found if i.data["county"] == "Hyde")
    assert hyde.data["vote_center"] is True and hyde.data["list_updated"] == "10/25/2018"
    wall = next(i for i in found if i.data["list"] == source.SATELLITE and i.data["city"] == "Wall")
    assert wall.key == "2018 absentee satellite sites | Pennington | Wall City Finance Office"
    assert found.covers("2026-07-28 Runoff Election | Aurora | Precinct-1 | x")
    assert not found.covers("2026-11-03 General Election | Aurora |  | x")
    assert len({i.key for i in found}) == len(found)


def test_satellite_staff_named_on_the_list_are_mentioned(no_pause, db):
    from records.models import Record, RecordSet

    found = source.items(FixtureFetcher(), today=date(2026, 11, 16))
    rs = RecordSet.objects.create(slug="polling-places", name="P", dataset="polling-places")
    names = []
    for item in found:
        if item.data["list"] == source.SATELLITE:
            names += [m.name for m in source.mentions(Record(recordset=rs, key=item.key,
                                                             data=item.data))]  # fmt: skip
    assert names == ["Carolyn Anderson", "Stacia Tallon"]


def test_the_table_knows_every_list_by_any_of_its_addresses():
    assert source.known(source.ASSETS + "/pollingplacesg22.PDF").slug == "2022-11-08-general"
    assert source.known(source.ASSETS + "/2024%20Assets/2024AbsenteeSatelliteSites.pdf").slug == (
        "2024-absentee-satellite-sites")  # fmt: skip
    assert len({p.url for p in source.LISTS}) == len(source.LISTS)
