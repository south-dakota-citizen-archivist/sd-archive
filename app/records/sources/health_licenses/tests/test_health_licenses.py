"""Health licensing boards' lists: each kind against pages cut from the real lists, checked by
hand against them.

- speech-language-pathology-page-1.pdf: the portal's roster (header over three lines).
- funeral-directors-page-1.pdf: the same layout, its Level cell wrapped above and below.
- funeral-establishments-page-1.pdf: the portal's establishments roster.
- massage-therapy-pages-1-2.pdf: a posted list whose second page has no header.
- nursing-facility-administrators-last-page.pdf: header words heading two columns each, and a
  footer.
- midwives.pdf: the midwives' text list, whole.
- chiropractic.csv, optometry.csv: the licensee apps' downloads, first rows.
"""

from pathlib import Path

import pdfplumber
import pytest

from records.sources.health_licenses import boards, source, tables

FIXTURES = Path(__file__).parent / "fixtures"


def read(slug: str, fixture: str) -> tuple[list[dict], dict]:
    return source.read_list(boards.BY_SLUG[slug], (FIXTURES / fixture).read_bytes())


def test_a_portal_roster():
    rows, checks = read("speech-language-pathology", "speech-language-pathology-page-1.pdf")
    first = rows[0]
    assert (first["name"], first["last_name"], first["license_number"], first["license_type"]) \
        == ("Jill Aarstad", "Aarstad", "006-SLP", "SLP")  # fmt: skip
    assert first["issued"] == "2013-10-01" and first["expires"] == "2027-10-01"
    assert first["discipline"] == "No" and first["disciplined"] is False
    assert "lines in no row" not in checks and "license numbers in the text not read" not in checks


def test_a_wrapped_cell_joins_its_row():
    rows, checks = read("funeral-directors", "funeral-directors-page-1.pdf")
    assert rows[0]["name"] == "Whitney Ahrens" and rows[0]["license_type"] == "Funeral Director"
    assert rows[0]["license_number"] == "5234" and rows[1]["name"] == "Mitchell Aman"
    assert all(r["license_type"] == "Funeral Director" for r in rows)
    assert "lines in no row" not in checks


def test_establishments():
    rows, _ = read("funeral-establishments", "funeral-establishments-page-1.pdf")
    assert rows[0]["name"] == "Anderson Funeral Home" and rows[0]["city"] == "Canton"
    assert rows[0]["holder"] == "establishment" and rows[0]["expires"] == "2027-12-31"
    assert rows[1]["name"] == "Anderson-Jones-Corcoran Funeral Chapel"
    assert source._places(rows[1]) == ["0400000US46", "1600000US4627260"]  # Harrisburg


def test_a_page_without_a_header_continues_the_table():
    rows, checks = read("massage-therapy", "massage-therapy-pages-1-2.pdf")
    assert rows[0]["license_number"] == "MT11475" and rows[0]["first_name"] == "Kjirsti"
    assert rows[0]["city"] == "Sioux Falls" and rows[0]["state"] == "SD"
    page_2 = next(r for r in rows if r["license_number"] == "MT11075")
    assert page_2["name"] == "Cindy Bechard" and page_2["state"] == "CO"
    assert "license numbers in the text not read" not in checks


def test_repeated_header_words_and_a_footer():
    rows, checks = read("nursing-facility-administrators",
                        "nursing-facility-administrators-last-page.pdf")  # fmt: skip
    last = rows[-1]
    assert last["name"] == "Timothy Yeaton" and last["license_number"] == "03630"
    assert last["expires"] == "2026-12-31" and last["issued"] == "2011-04-20"
    assert last["details"] == {"facility_city": "Flandreau", "facility_state": "SD",
                               "facility_phone": "(605) 997-2481",
                               "preceptor_through": "2026"}  # fmt: skip
    assert "lines in no row" not in checks  # "Last updated: …" is the footer


def test_midwives():
    rows, checks = read("certified-professional-midwives", "midwives.pdf")
    assert len(rows) == 16 and not checks
    carlson = rows[0]
    assert (carlson["name"], carlson["license_number"], carlson["city"], carlson["state"]) == (
        "Eileen Carlson", "001901", "Marshall", "MN")  # fmt: skip
    assert carlson["issued"] == "2019-03-01" and carlson["expires"] == "2027-03-01"
    johnson = next(r for r in rows if r["name"] == "Olivia Johnson")
    assert johnson["license_type"] == "Student midwife" and johnson["license_number"] == "002501S"
    assert johnson["details"]["preceptor"] == ("Alaina Kerkhove CPM License #001906; "
                                               "Eileen Carlson CPM License #001901")  # fmt: skip
    arnold = next(r for r in rows if r["name"] == "Elaine Arnold")
    assert arnold["status"] == "Lapsed" and arnold["details"]["status_date"] == "2025-12-01"


def test_the_apps_csv():
    chiro, _ = read("chiropractic", "chiropractic.csv")
    assert chiro[0]["name"] == "Kristen D Ahlers" and chiro[0]["issued"] == "2016-01-04"
    assert chiro[0]["disciplined"] is False and chiro[0]["details"] == {
        "GoodStanding": "True", "Acupuncture": "True"}  # fmt: skip
    opt, _ = read("optometry", "optometry.csv")
    assert opt[1]["name"] == "Katelin Albers-Kleinsasser"
    assert (opt[1]["city"], opt[1]["state"]) == ("Huron", "SD")
    assert opt[1]["status"] == "Active" and opt[1]["license_type"] == "TPA"


def test_keys_by_type_where_numbers_repeat():
    pt = boards.BY_SLUG["physical-therapy"]
    assert source.key(pt, {"license_type": "PTA", "license_number": "0546"}) == \
        "physical-therapy|PTA|0546"  # fmt: skip
    slp = boards.BY_SLUG["speech-language-pathology"]
    assert source.key(slp, {"license_type": "SLP", "license_number": "006-SLP"}) == \
        "speech-language-pathology|006-SLP"  # fmt: skip


def test_finding_a_list_on_its_page():
    html = '<a href="/healthdept/speechpath/verify/roster.pdf?tim=1794868279">Roster</a>'
    assert boards.find(html, "https://www.sdboards.org/healthdept/SpeechPath/verify/",
                       r"roster\.pdf") == \
        "https://www.sdboards.org/healthdept/speechpath/verify/roster.pdf"  # fmt: skip
    with pytest.raises(boards.NotLinked):
        boards.find("<a href='/other.pdf'>x</a>", "https://doh.sd.gov/", r"roster\.pdf")


def test_a_changed_header_is_refused():
    with pdfplumber.open(FIXTURES / "speech-language-pathology-page-1.pdf") as pdf, \
            pytest.raises(tables.LayoutChanged):  # fmt: skip
        tables.read(pdf, {**boards.ROSTER, "Supervisor": "supervisor"})


@pytest.mark.network
def test_the_live_lists(tmp_path):
    import sdfetch

    found = source.items(sdfetch.Fetcher(tmp_path, delay=1.0))
    counts = found.checks["licenses by list"]
    assert set(counts) == {x.slug for x in boards.LISTS} and sum(counts.values()) > 4000
