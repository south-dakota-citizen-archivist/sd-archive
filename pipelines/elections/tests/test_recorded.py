"""The scraper against the site's real pages and API, recorded once (tests/cassettes/) and
replayed. Re-record when the site changes: just record pipelines/elections -k recorded"""

import capture
import discover
import pytest

import sdfetch

GENERAL_2024 = 684


@pytest.mark.vcr
def test_discover_reads_an_election_and_skips_an_empty_eid(tmp_path):
    f = sdfetch.Fetcher(tmp_path, delay=0)
    row = discover.parse(GENERAL_2024, discover.fetch(f, GENERAL_2024, refresh=False))
    assert row["date"] == "2024-11-05" and "General" in row["title"]
    assert row["status"] == "unofficial"  # the site never relabeled it
    assert discover.parse(99999, discover.fetch(f, 99999, refresh=False)) is None


@pytest.mark.vcr
def test_capture_finds_races_and_reads_precinct_results(tmp_path, monkeypatch):
    monkeypatch.setattr(capture, "RAW", tmp_path)
    f = capture.Fetcher(GENERAL_2024, delay=0, refresh=False)
    home = f.get(f"{capture.SITE}default.aspx?eid={GENERAL_2024}", f.root / "home.html")
    assert capture.turnout(home)["ballots_cast"] > 400_000

    statewide = [u for u in capture.links(home, GENERAL_2024) if "type=SW" in u]
    assert statewide, "the home page links to statewide results"
    page = f.get(statewide[0], f.root / "statewide.html")
    races = capture.calls(page)
    assert races and {"type", "race", "osn", "party"} <= races[0].keys()

    # one race's county totals, and its precincts in one county (statewide precincts would
    # make the recording too big to keep)
    race = {**races[0], "eid": GENERAL_2024}

    def api(category, county):
        url = capture.api_url(race, category, county)
        return capture.rows_of(f.get(url, capture.api_path(f.root, race, category, county)))

    counties = api("CTY", "0")
    assert len(counties) >= 66 and all(isinstance(r, dict) for r in counties)
    precincts = api("PREC", "02")  # Beadle
    assert precincts and all(isinstance(r, dict) for r in precincts)
