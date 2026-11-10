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
    assert exports.SHEET.match("1556State Representatives - RE")["recount"]
    # A long name cut off mid-word isn't a recount
    assert not exports.SHEET.match("98Referendum  A RESOLUTION RE")["recount"]
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


def test_primary_sheet_names_carry_the_party():
    m = exports.SHEET.match("REP14452Governor")
    assert (m["party"], m["race"], m["name"]) == ("REP", "14452", "Governor")
    m = exports.SHEET.match("NON13017City Council  RECO")
    assert (m["party"], m["race"], bool(m["recount"])) == ("NON", "13017", True)
    assert exports.SHEET.match("REPState Rep District 06") is None  # 2020: no race ID


def write_workbook(path, sheets):
    with pd.ExcelWriter(path) as w:
        for name, rows in sheets.items():
            pd.DataFrame(rows).to_excel(w, sheet_name=name, header=False, index=False)


RACE = HEADER + [
    ["Sheriff\nLawrence", "Precinct", "Ann Smith", None],
    [None, "Precinct-01", "430", None],
    [None, "TOTALS", "430", None],
]


def test_repeated_sheets(tmp_path):
    other = [row[:] for row in RACE]
    other[-1][2] = other[-2][2] = "431"
    path = tmp_path / "Lawrence.xlsx"
    write_workbook(path, {"651Sheriff": RACE, "651Sheriff_1": RACE, "652Auditor": RACE,
                          "652Auditor_1": other})  # fmt: skip
    repeats = []
    rows = exports.read_workbook(path, repeats)
    assert [r["sheet"] for r in rows if r["precinct"]] == [
        "651Sheriff", "652Auditor", "652Auditor_1"
    ]  # fmt: skip
    assert repeats == [("Lawrence.xlsx", "652Auditor_1")]
    assert {r["county"] for r in rows} == {"Lawrence"}


def test_race_types(tmp_path):
    (tmp_path / "exports").mkdir()
    write_workbook(tmp_path / "exports" / "Legislative-Export.xlsx", {"1510State Senator": RACE})
    write_workbook(
        tmp_path / "exports" / "Statewide-Export.xlsx",
        {"1586United States Senator": RACE, "29Constitutional Amendment K ": RACE},
    )
    assert exports.race_types(tmp_path) == {"1510": "LEG", "1586": "SWR", "29": "BQ"}
