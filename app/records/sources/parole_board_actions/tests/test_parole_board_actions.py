"""Parole board action reports: each layout against pages cut from real reports, checked by
hand against them, and the meetings page that lists them.

- decisions-by-offender-2016-05.pdf: May 2016's first page and its summary page.
- public-release-2023-10-19.pdf: October 19, 2023's first page and its summary page.
- scanned-2024-08-page-1.pdf, with the three OCR engines' pieces for it: August 2024's first page.
- meetings-2020.html: the board's meetings page, 2020.
"""

import json
from collections import Counter
from pathlib import Path

import pdfplumber
import pytest

from records.sources.parole_board_actions import (
    county_codes,
    listing,
    ocr,
    offender,
    release,
    scanned,
    source,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_the_meetings_page_lists_each_meetings_reports():
    meetings = listing.meetings((FIXTURES / "meetings-2020.html").read_text())
    december = next(m for m in meetings if m.date and m.date.isoformat() == "2020-12-17")
    reports = [d for d in december.documents if listing.is_report(d)]
    assert [d.title for d in reports] == ["December 2020 actions"]
    assert reports[0].url.endswith("ParoleBoardActionsDecember2020.pdf")
    assert not any(listing.is_report(d) for d in december.documents if "agenda" in d.title)
    assert 2015 in listing.years((FIXTURES / "meetings-2020.html").read_text())


def test_the_older_layout():
    with pdfplumber.open(FIXTURES / "decisions-by-offender-2016-05.pdf") as pdf:
        cases = offender.cases(pdf)
        summary = offender.summary(pdf.pages[-1].extract_text())
    assert len(cases) == 6 and [c.cells["doc"] for c in cases][:2] == [["37737"], ["13123"]]
    adams = cases[0]
    assert adams.cells["name"] == ["ADAMS, MARQUEZ", "DSP [WCFD-3RD-301-C]"]
    assert adams.cells["race"] == ["BLA", "M"] and adams.cells["os"] == ["NEW"]
    assert adams.crimes == [{"crime": "BURGLARY 3RD", "transaction": "16982",
                             "county_code": "PE"}]  # fmt: skip
    assert offender.hearings(adams.cells["decision"]) == ([("Grant", "Parole Hearing")], "")
    assert adams.cells["details"] == ["BOARD ORDER:", "CTP Rapid City."]
    ambro = cases[2]  # two crimes, the second without its own transaction number
    assert [c["crime"] for c in ambro.crimes] == [
        "Unauthorized ingestion of controlled substance", "GRAND THEFT < $2,500.00"]  # fmt: skip
    assert [c["county_code"] for c in ambro.crimes] == ["MD", "MD"]
    assert ambro.cells["review"] == ["01-AUG-16"]
    anderson = cases[5]  # a decision that wraps over lines before its hearing type
    assert offender.hearings(anderson.cells["decision"]) == (
        [("Deny Earned Final Discharge", "Earned Discharge Hearing")], "")  # fmt: skip
    assert summary["total_inmates"] == 230 and summary["outcomes"]["Deny"] == 79
    assert sum(summary["outcomes"].values()) == 239


def test_a_cell_with_two_hearings():
    cell = ["Non-Compliant", "Non-", "compliance", "Hearing", "Deny", "Parole", "Hearing"]
    assert offender.hearings(cell) == ([("Non-Compliant", "Non-compliance Hearing"),
                                        ("Deny", "Parole Hearing")], "")  # fmt: skip
    assert offender.hearings(["Appeal of Parole", "Date Hearing"]) == (
        [(None, "Appeal of Parole Date Hearing")],
        "",
    )  # no decision: the summary's N/A
    assert offender.hearings(["Something", "New"])[1] == "Something New"  # never guessed


def test_the_newer_layout():
    with pdfplumber.open(FIXTURES / "public-release-2023-10-19.pdf") as pdf:
        cases = release.cases([(i + 1, p.extract_words(x_tolerance=1.5))
                               for i, p in enumerate(pdf.pages)])  # fmt: skip
    first, amdahl = cases[0], cases[1]
    assert first.cells == {"doc": "65372", "name": "ALVIZO-ALLISON, TOMAS", "race": "HOL",
                           "sex": "M"}  # fmt: skip
    assert first.counties == ["Union County Court"]
    assert first.hearings == [{"hearing_type": "Parole Hearing", "decision": "Grant"}]
    assert amdahl.counties == ["Codington County Court", "Brookings County Court",
                               "Codington County Court", "Grant County Court"]  # fmt: skip
    assert amdahl.hearings == [{"hearing_type": "PV Waiver Hearing", "decision": "Revoke",
                                "review": "01-MAY-24"}]  # fmt: skip


def test_a_scan_read_by_two_engines(monkeypatch):
    def pieces(pdf, index, engine, key=None, cached=False):
        return json.loads((FIXTURES / f"scanned-2024-08-page-1.{engine}.json").read_text())

    monkeypatch.setattr(ocr, "pieces", pieces)
    cases, _ = scanned.read(FIXTURES / "scanned-2024-08-page-1.pdf")
    abdo = cases[0]
    assert abdo["fields"]["doc"] == "22771" and abdo["fields"]["name"] == "ABDO, JOHN"
    assert abdo["fields"]["county_courts"] == ("Charles Mix County", "Charles Mix County")
    assert abdo["fields"]["hearings"] == (("Parole Hearing", "Deny", "01-AUG-25"),)
    # every field the engines read differently is kept apart, with both readings
    for c in cases:
        for name in c["unconfirmed"]:
            if name.startswith("hearing "):  # "hearing 2 review": that part left empty
                _, n, part = name.split()
                i, j = int(n) - 1, ("type", "decision", "review").index(part)
                assert c["fields"]["hearings"][i][j] is None
                name = "hearings"
            else:
                assert c["fields"][name] is None
            assert set(c["readings"][name]) == set(scanned.ENGINES)


def test_review_dates_and_hearings_voted_part_by_part():
    assert scanned._review("O1-MAY-25") == "01-MAY-25"
    assert scanned._review("01-MAX-25") == "01-MAY-25"
    assert scanned._review("01-MAY -25") == "01-MAY-25"
    assert scanned._review("Wa OF:I") == "Wa OF:I"  # not a date: kept as read
    first = ("Early Discharge Hearing", "Continue", "01-MAY-24")
    second = ("Early Discharge Hearing", "Grant Partial Early Discharge", "")
    # two hearings, the third engine's second decision garbled: both kept, one part empty
    hearings, unsettled = scanned._hearings({
        "rapidocr": (first, second),
        "textra": (first, ("Early Discharge Hearing", "Early Discharge", "")),
        "tesseract_words": (("Early Discharge Hearing", "Continue", ""), second)})  # fmt: skip
    assert hearings == (first, ("Early Discharge Hearing", "Grant Partial Early Discharge",
                                None))  # fmt: skip
    assert unsettled == []  # no engine read a second review date: printed blank
    assert scanned._hearings({"rapidocr": (first,), "textra": (first, second),
                              "tesseract_words": (first, second, second)}) == (
        None, ["hearings"])  # one, two and three hearings: no count agreed  # fmt: skip


def test_county_codes_from_people_in_both_layouts(tmp_path, monkeypatch):
    monkeypatch.setattr(county_codes, "DECISIONS", tmp_path / "codes.toml")
    older = {str(i): {"PE"} for i in range(9)} | {"9": {"PE"}, "10": {"XQ"}}
    newer = {str(i): {"Pennington"} for i in range(9)} | {"9": {"Minnehaha"}, "10": {"Lake"}}
    found = county_codes.evidence(older, newer)
    codes = county_codes.mapping(Counter({"PE": 40, "XQ": 2, "BH": 3}), found)
    assert codes["PE"] == {"county": "Pennington", "how": "evidence", "printed": 40,
                           "evidence": {"Pennington": 9, "Minnehaha": 1}}  # fmt: skip
    assert codes["XQ"]["county"] is None  # one person, and its letters don't fit Lake
    assert codes["BH"]["county"] is None  # no one: left as printed
    assert "Bon Homme" in county_codes.candidates("BH", Counter())


def test_places_and_dates():
    assert source._county("Charles Mix County") == "Charles Mix"
    assert source._county("Out of State") is None
    assert source._date("01-AUG-25") == "2025-08-01" and source._date("") is None
    assert source._places(["Pennington"], "2020-01-01") == ["0400000US46", "0500000US46103"]


@pytest.mark.network
def test_the_live_meetings_page(tmp_path):
    import sdfetch

    found = source.meetings(sdfetch.Fetcher(tmp_path, delay=1.0))
    assert sum(listing.is_report(d) for m in found for d in m.documents) >= 150
