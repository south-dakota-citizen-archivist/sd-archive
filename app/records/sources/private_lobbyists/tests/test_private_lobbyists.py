"""Private lobbyists: two real registration pages (checked by hand) read, a correction applied
and kept on the record, and a run tied to the lobbyist's and employer's cities."""

from pathlib import Path

from records.models import Record, RecordSet
from records.run import run_source
from records.sources import _lobbyists as L
from records.sources.private_lobbyists import source

FIXTURES = Path(__file__).parent / "fixtures"
CN_2014 = "007123056177108222156232189146127011085116114083"
CN_2015 = "253020026218211199083085079151099144074091241104"
PAGES = {
    CN_2014: (FIXTURES / "detail-2014.html").read_bytes(),
    CN_2015: (FIXTURES / "detail-2015-date-fix.html").read_bytes(),
}


def url(cn: str) -> str:
    return f"{L.DETAIL}?CN={cn}"


def test_a_registration_page():
    d = L.detail(PAGES[CN_2014], url(CN_2014))
    assert {k: d[k] for k in ("registration_guid", "year", "registration_number",
                              "lobbyist_name", "lobbyist_status", "lobbyist_employment_date",
                              "lobbyist_phone", "lobbyist_type", "employer_name",
                              "employer_agent_name", "employer_registration_status")} == {
        "registration_guid": CN_2014, "year": 2014, "registration_number": "059503",
        "lobbyist_name": "DARLA POLLMAN ROGERS", "lobbyist_status": "ACTIVE",
        "lobbyist_employment_date": "2014-01-15", "lobbyist_phone": "(605) 280-2014",
        "lobbyist_type": "PRIVATE LOBBYIST", "employer_name": "NAIFA-SD",
        "employer_agent_name": "ADAM WEEMAN",
        "employer_registration_status": "PENDING AUTHORIZATION",
    }  # fmt: skip
    assert d["employer_authorization_date"] is None
    assert d["employer_address"] == "401 E. 8TH ST, SUITE 214-512 SIOUX FALLS, SD 57103 USA"
    parts = ("employer_address_", "lobbyist_address_")
    assert {k: v for k, v in d.items() if k.startswith(parts)} == {
        "lobbyist_address_street": None, "lobbyist_address_city": "PIERRE",
        "lobbyist_address_state": "SD", "lobbyist_address_zip": "57501",
        "lobbyist_address_country": "USA",
        "employer_address_street": "401 E. 8TH ST, SUITE 214-512",
        "employer_address_city": "SIOUX FALLS", "employer_address_state": "SD",
        "employer_address_zip": "57103", "employer_address_country": "USA",
    }  # fmt: skip
    assert [(f["filing_type"], f["filing_date"]) for f in d["filings"]] == [
        ("Lobbyist Expense Report", "2014-01-15"), ("Lobbyist Expense Report", "2014-03-14"),
        ("Employer Expense Report", "2014-01-15"), ("Employer Expense Report", "2014-03-14"),
    ]  # fmt: skip


def test_address_parts():
    parts = L.address_parts
    assert parts(["SD APO", "(US)"], "a")["a_country"] == "US"
    for written in ("SIOUX FALLS, , SD 57105", "SIOUX FALLS, SD, SD 57105",
                    "SIOUX FALLS, SD 57105, SD 57105", "SIOUX FALLS,, SD 57105"):  # fmt: skip
        assert parts(["705 E 41ST ST", written, "USA"], "a") == {
            "a_street": "705 E 41ST ST", "a_city": "SIOUX FALLS", "a_state": "SD",
            "a_zip": "57105", "a_country": "USA",
        }, written  # fmt: skip
    assert parts(["500 EAST CAPITOL AVENUE", ", SD 57501"], "a") == {
        "a_street": "500 EAST CAPITOL AVENUE", "a_city": None, "a_state": "SD",
        "a_zip": "57501", "a_country": None,
    }  # fmt: skip
    assert parts(["-", ", SD"], "a") == {"a_street": None, "a_city": None, "a_state": "SD",
                                         "a_zip": None, "a_country": None}  # fmt: skip
    assert parts([], "a") == dict.fromkeys(("a_street", "a_city", "a_state", "a_zip",
                                            "a_country"))  # fmt: skip


def test_a_mistyped_year_is_corrected_and_kept_on_the_record():
    from records import fixes

    d = L.detail(PAGES[CN_2015], url(CN_2015))
    assert d["employer_authorization_date_raw"] == "2/18/2105"
    assert fixes.apply(d, source.FIXES) == [{
        "field": "employer_authorization_date", "published": "2105-02-18",
        "corrected": "2015-02-18",
        "reason": "The page gives the authorization date's year as 2105.",
    }]  # fmt: skip
    assert d["employer_authorization_date"] == "2015-02-18"


def test_surnames_cover_compound_names():
    assert source.surnames(["JOHN BRINGS PLENTY", "MARY VAN CAMP JR"]) == [
        "BRINGS PLENTY", "CAMP", "PLENTY", "VAN CAMP"]  # fmt: skip


def test_the_check_against_the_printable_list():
    listed = [{"lobbyist_name": "A B", "year": "2014"}, {"lobbyist_name": "C D", "year": "2015"}]
    found = [{"lobbyist_name": "A B", "year": 2014}, {"lobbyist_name": "E F", "year": 2015}]
    assert source.check(listed, found) == {"listed": 2, "found": 2, "missing": ["C D 2015"],
                                           "missing_count": 1, "extra_count": 1}  # fmt: skip


class FixtureSession:
    def __init__(self, fetcher):
        pass

    def search(self, last_name=""):
        return last_name.encode()

    def printable(self):
        return b"%PDF"


class FixtureFetcher:
    store = None

    def request(self, method, url, **kwargs):
        return type("R", (), {"content": PAGES[L.cn(url)]})()


def test_run(db, sd_places, monkeypatch):
    monkeypatch.setattr(L, "Session", FixtureSession)
    monkeypatch.setattr(L, "pdf_rows", lambda pdf, kind: [
        {"lobbyist_name": "DARLA POLLMAN ROGERS", "year": "2014"},
        {"lobbyist_name": "HELEN FITZGERALD", "year": "2015"},
        {"lobbyist_name": "ANN BOLMAN", "year": "2024"},  # a public lobbyist, left out
    ])  # fmt: skip
    found = {b"ROGERS": [url(CN_2014)], b"FITZGERALD": [url(CN_2015)]}
    monkeypatch.setattr(L, "result_rows", lambda page: [
        {"url": u, "year": u == url(CN_2014) and "2014" or "2015"} for u in found.get(page, [])
    ])  # fmt: skip
    RecordSet.objects.create(slug="private-lobbyists", name="Private lobbyists",
                             dataset="lobbyists")  # fmt: skip
    run = run_source("private-lobbyists", fetcher=FixtureFetcher())
    assert run.added == 2
    assert run.checks["missing_count"] == 0
    rogers = Record.objects.get(key=url(CN_2014))
    assert sorted(rogers.places.values_list("short_name", flat=True)) == [
        "Pierre", "Sioux Falls", "South Dakota"]  # fmt: skip
    assert rogers.documents.count() == 1  # both reports link to the same file
    fitzgerald = Record.objects.get(key=url(CN_2015))
    assert fitzgerald.data["corrections"][0]["corrected"] == "2015-02-18"
    assert list(fitzgerald.places.values_list("short_name", flat=True)) == ["South Dakota"]


def test_export(db, tmp_path):
    import csv
    import io

    from django.core.management import call_command
    from django.utils import timezone

    from records import fixes

    private = RecordSet.objects.create(
        slug="private-lobbyists", name="Private", dataset="lobbyists"
    )
    public = RecordSet.objects.create(slug="public-lobbyists", name="Public", dataset="lobbyists")
    now = timezone.now()
    for cn in (CN_2014, CN_2015):
        d = L.detail(PAGES[cn], url(cn))
        if made := fixes.apply(d, source.FIXES):
            d["corrections"] = made
        Record.objects.create(recordset=private, key=url(cn), data=d, first_seen=now,
                              dropped_at=now if cn == CN_2015 else None)  # fmt: skip
    data = {"year": 2012, "lobbyist_name": "A B", "agency": "C", "agency_address_city": "PIERRE"}
    Record.objects.create(recordset=public, key="2012 A B | C", first_seen=now, data=data)
    call_command("export_lobbyists", out=str(tmp_path), stdout=io.StringIO())

    def rows(name):
        with (tmp_path / name).open() as f:
            return list(csv.DictReader(f))

    fitzgerald, rogers = rows("lobbyists-private.csv")  # newest year first
    assert rogers["lobbyist_name_given"] == "DARLA" and rogers["lobbyist_name_family"] == "ROGERS"
    assert rogers["employer_address_city"] == "SIOUX FALLS" and rogers["filings"] == "4"
    assert rogers["dropped_at"] == "" and fitzgerald["dropped_at"]
    assert fitzgerald["employer_authorization_date"] == "2015-02-18"
    assert "2105-02-18" in fitzgerald["corrections"]
    filings = rows("lobbyists-private-filings.csv")
    assert len(filings) == 6 and filings[-1]["employer_name"] == "NAIFA-SD"
    (row,) = rows("lobbyists-public.csv")
    assert (row["agency_address_city"], row["listed_as"]) == ("PIERRE", "public")
