import capture
import exports
import pandas as pd


def sheet(rows):
    return pd.DataFrame(rows, dtype=object)


HEADER = [
    ["2026 Unofficial General Election Results", None, None, None],
    ["State of South Dakota", None, None, None],
    ["Precincts 671 of 671 Reporting", None, None, None],
    ["Lawrence County", None, None, None],
    ["Downloaded at 11/10/2026 5:31:15 AM", None, None, None],
    [None, None, None, None],
]


def test_read_sheet_precincts_and_totals():
    rows = exports.read_sheet(
        sheet(
            HEADER
            + [
                ["State Senator\n31", "Precinct", "Ann  Smith", "Bo O'Dea "],
                [None, "Precinct-01", "430", "1,286"],
                [None, "Precinct-02", "12", "0"],
                [None, "TOTALS", "442", "1,286"],
            ]
        )
    )
    assert [(r["precinct"], r["candidate"], r["votes"]) for r in rows] == [
        ("Precinct-01", "Ann Smith", 430),
        ("Precinct-01", "Bo O'Dea", 1286),
        ("Precinct-02", "Ann Smith", 12),
        ("Precinct-02", "Bo O'Dea", 0),
        (None, "Ann Smith", 442),
        (None, "Bo O'Dea", 1286),
    ]
    assert rows[0]["race_title"] == "State Senator"
    assert rows[0]["race_subtitle"] == "31"
    assert rows[2]["precinct_seq"] == 2


def test_read_sheet_blank_precinct_names_2012():
    rows = exports.read_sheet(
        sheet(
            HEADER
            + [
                ["Presidential Electors", "Precinct", "Obama", "Romney"],
                [None, None, "104", "201"],
                [None, None, "44", "90"],
                [None, "TOTALS", "148", "291"],
            ]
        )
    )
    assert [(r["precinct"], r["precinct_seq"], r["votes"]) for r in rows[:4]] == [
        ("", 1, 104),
        ("", 1, 201),
        ("", 2, 44),
        ("", 2, 90),
    ]


def test_recount_sheet_names():
    for name in ("1556State Representatives  RE", "2628County Commissioner  RECO",
                 "2281County Auditor  RECOUNT"):  # fmt: skip
        assert exports.SHEET.match(name)["recount"], name
    m = exports.SHEET.match("4265Presidential Electors")
    assert (m["race"], m["recount"]) == ("4265", None)


def test_normalize_live_drops_the_eid():
    url = "ResultsSW.aspx?type=CTYALL&amp;eid=684&map=CTY"
    assert capture.normalize(url, 774) == (
        "https://electionresults.sd.gov/ResultsSW.aspx?eid=774&map=CTY&type=CTYALL"
    )
    assert capture.normalize(url, 774, live=True) == (
        "https://electionresults.sd.gov/ResultsSW.aspx?map=CTY&type=CTYALL"
    )


def test_api_url_live_uses_the_current_api():
    race = {"type": "SWR", "race": "14412", "osn": "100", "party": "", "eid": 774}
    assert "GetMapDataArchive" in capture.api_url(race, "PREC", "0")
    assert "electionID=774" in capture.api_url(race, "PREC", "0")
    live = capture.api_url(race, "PREC", "0", live=True)
    assert "/GetMapData?" in live and "electionID" not in live


def test_election_date_and_turnout():
    page = (
        '<input type="hidden" name="ctl00$hidElectionDate" id="hidElectionDate" '
        'value="11/3/2026" /><div>Ballots Cast <span>310,298</span> Registered Voters 628,563'
    )
    assert capture.election_date(page) == "2026-11-03"
    assert capture.turnout(page) == {"ballots_cast": 310298, "registered_voters": 628563}


def test_calls_parses_race_buttons():
    page = "BuildPrecincts('SWR', '4265', '100', '01', '') BuildCounty('BQ', '66', '903', '')"
    assert capture.calls(page) == [
        {"fn": "BuildPrecincts", "type": "SWR", "race": "4265", "osn": "100", "county": "01",
         "party": ""},
        {"fn": "BuildCounty", "type": "BQ", "race": "66", "osn": "903", "county": "",
         "party": ""},
    ]  # fmt: skip
