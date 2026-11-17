"""Health licensing boards' disciplinary actions: each board's page (its content, cut from the
real page) and two of the Board of Pharmacy's reports, checked by hand against them."""

from pathlib import Path

import pdfplumber
import pytest

from records.sources.health_board_actions import pages, reports, source

FIXTURES = Path(__file__).parent / "fixtures"


def read(board: str) -> list[dict]:
    html = (FIXTURES / f"{board}.html").read_text()
    got, unread = source.read_page(board, html, source.DOH + source.BOARDS[board][1])
    assert unread == []  # every entry that looks like an action is read
    return [a for a, _ in got]


def test_optometry():
    found = read("optometry")
    assert len(found) == 10
    bartell = found[0]
    assert (bartell["name"], bartell["action"], bartell["effective"], bartell["year"]) == (
        "Bartell, Cesar", "Voluntary Surrender of License", "2024-03-18", 2024)  # fmt: skip
    weiss = next(a for a in found if a["year"] == 2012 and a["name"] == "Melanie Weiss")
    # "Effective Date: October 30 - End Date: October 31, 2013", under 2012
    assert weiss["effective"] == "2012-10-30" and weiss["ended"] == "2013-10-31"


def test_funeral_service_with_orders():
    html = (FIXTURES / "funeral-service.html").read_text()
    got, _ = source.read_page("funeral-service", html, source.DOH + "funeral/")
    oster = [(a, docs) for a, docs in got if a["name"] == "Taylor Oster"]
    assert [a["action"] for a, _ in oster] == [
        "Consent Order", "Voluntary Surrender of License", "Agreed Stipulation"]  # fmt: skip
    assert oster[0][0]["license_number"] == "1388" and oster[0][0]["effective"] == "2022-11-17"
    assert (
        oster[0][1][0].url
        == "https://doh.sd.gov/media/5oyfvzap/disciplinary_oster_consentorder.pdf"
    )
    scott = next(a for a, _ in got if a["name"] == "Jessica Scott")
    assert scott["credential"] is None and scott["effective"] == "2024-10-17"


def test_audiology_whose_parts_run_together():
    found = read("hearing-aid-dispensers-audiologists")
    ward, sneeden = found[1], found[2]
    assert (ward["name"], ward["city"], ward["license_number"], ward["action"]) == (
        "Richard A. Ward, III", None, "408H", "Letter of Reprimand")  # fmt: skip
    assert (sneeden["name"], sneeden["city"], sneeden["effective"]) == (
        "James Sneeden", "Watertown", "2009-03-06")  # fmt: skip
    assert sneeden["details"] == {"license expired": "06/30/14"}


def test_nursing_facility_administrators_in_five_lines():
    ward = read("nursing-facility-administrators")[0]
    assert (ward["name"], ward["city"], ward["state"], ward["action"]) == (
        "Joe Ward", "Sioux Falls", "SD", "Suspension of License")  # fmt: skip
    assert ward["effective"] == "2012-10-31" and ward["ended"] == "2013-05-24"
    assert ward["nature"] == "Falsifying records and unprofessional conduct"
    assert ward["year"] == 2012  # no year headings: its date's


def test_chiropractic_and_a_month_without_a_day():
    found = read("chiropractic")
    martens = found[0]
    assert (martens["name"], martens["city"], martens["license_number"]) == (
        "Joel C. Martens", "Hartford", "1327")  # fmt: skip
    assert martens["action"] == "Consent Agreement and Order"
    jones = next(a for a in found if a["name"] == "Larry Jones")
    assert jones["effective"] is None and jones["effective_as_listed"] == "December 2011"
    assert jones["year"] == 2011


def test_podiatry():
    [monroe] = read("podiatry")
    assert (monroe["license_number"], monroe["action"], monroe["effective"]) == (
        "121", "License Revoked", "2011-07-07")  # fmt: skip


def test_pharmacy_reports():
    with pdfplumber.open(FIXTURES / "pharmacy-disciplinary2005.pdf") as pdf:
        [tokach] = reports.actions(pdf.pages[0].extract_text())
    assert (tokach["name"], tokach["credential"], tokach["license_number"]) == (
        "Mark Tokach", "R.Ph.", "R-4435")  # fmt: skip
    assert tokach["nature"].startswith("Violated laws") and tokach["nature"].endswith(
        "quarterly reports to the Board of Pharmacy.")  # fmt: skip
    with pdfplumber.open(FIXTURES / "pharmacy-munger2016.pdf") as pdf:
        [munger] = reports.actions(pdf.pages[0].extract_text())
    assert (munger["city"], munger["state"], munger["action"]) == (
        "De Smet", "SD", "Three year probation")  # fmt: skip
    store = reports.actions("Name: Walgreens\nCity: Sioux Falls\nLicense Number: 100-1925\n"
                            "Board Action: Probation\nEffective Date: May 10, 2026")  # fmt: skip
    assert store[0]["holder"] == "establishment"


def test_the_pharmacy_page_links_its_reports():
    html = (FIXTURES / "pharmacy.html").read_text()
    labels = [label for e in pages.entries(html) for label, _ in e.links
              if source.REPORT_LINK.search(label)]  # fmt: skip
    assert labels[0] == "2026 disciplinary report" and "2005 disciplinary report" in labels


def test_dates():
    assert source._date("September 29th, 2014", None) == "2014-09-29"
    assert source._date("December 2011", 2011) is None  # no day invented
    assert source._date("11/6/25", None) == "2025-11-06"


def test_keys():
    a = {"board": "pharmacy", "effective": "2026-05-10", "effective_as_listed": "May 10, 2026",
         "year": 2026, "license_number": "100-1925", "name": "Walgreens"}  # fmt: skip
    assert source.key(a) == "pharmacy|2026-05-10|100-1925"
    assert source.key(a | {"license_number": None}) == "pharmacy|2026-05-10|Walgreens"


@pytest.mark.network
def test_the_live_pages(tmp_path):
    import sdfetch

    found = source.items(sdfetch.Fetcher(tmp_path, delay=1.0))
    assert not [k for k in found.checks if k.endswith("entries not read")]
    assert sum(found.checks["actions by board"].values()) >= 60
