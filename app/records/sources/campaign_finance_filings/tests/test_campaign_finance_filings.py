"""Campaign finance committees: a real committee page parsed (checked by hand), and a run of
one committee into the database."""

from pathlib import Path

from entities.models import Mention
from records.models import Record, RecordSet
from records.run import run_source
from records.sources import get
from records.sources.campaign_finance_filings import parse, source

FIXTURES = Path(__file__).parent / "fixtures"
URL = "https://sdcfr.sdsos.gov/Search/SearchResults.aspx?cid=1111&rid=2423"


def test_a_committee_page():
    info, filings = parse.committee((FIXTURES / "committee-1111.html").read_bytes(), URL)
    assert (info["committee_name"], info["candidate_name"], info["status"]) == (
        "RICK FOR HOUSE",
        "Rick Stracqualursi",
        "Terminated",
    )
    assert info["treasurer_name"] == "Allen Nord MD"
    assert info["chair_email"] == "rstracqual@aol.com", "decoded from Cloudflare's protection"
    assert (info["cid"], info["rid"], info["website"]) == ("1111", "2423", None)
    assert [(f["report"] or f["label"], f["filed_on"], f["kind"]) for f in filings] == [
        ("Termination", "2023-01-27", "uploaded"), ("Year End", "2021-01-06", "uploaded"),
        ("Year End", "2021-01-04", "online"), ("Pre-General", "2020-10-17", "online"),
        ("Statement of Organization", "2020-05-21", "online"),
        ("Pre-Primary", "2020-05-18", "online"),
        ("Statement of Organization", "2020-04-27", "online"),
        ("Statement of Organization", "2020-04-11", "online")]  # fmt: skip
    assert filings[0]["url"] == "https://sdcfr.sdsos.gov/Document.aspx?DocumentID=5406&type=img"


def test_an_email_in_an_address_is_decoded_in_place():
    html = (b'<span id="MainContent_SchRsltCommitteInfo1_lblciHeader">X PAC</span>'
            b'<span id="MainContent_SchRsltCommitteInfo1_lblciAddress"><a class="__cf_email__" '
            b'data-cfemail="047677707665677571656844656b682a676b69">[email&#160;protected]</a>'
            b', Rapid City SD, 57702</span>')  # fmt: skip
    info, _ = parse.committee(html, URL)
    assert info["address"] == "rstracqual@aol.com, Rapid City SD, 57702"


def test_keys_are_the_committee_not_its_registration():
    class Old:
        key = "https://sdcfr.sdsos.gov/Search/SearchResults.aspx?cid=102&rid=1506"

    assert source.legacy_key(Old()) == "102"


def test_cfemail():
    assert parse.cfemail("047677707665677571656844656b682a676b69") == "rstracqual@aol.com"


def test_run(db, state, sd_counties, monkeypatch):
    from places.models import Jurisdiction

    Jurisdiction.objects.create(kind="place", geoid="4652980", geoidfq="1600000US4652980",
                                name="Rapid City city", short_name="Rapid City",
                                slug="rapid-city", parent=state)  # fmt: skip
    rs = RecordSet.objects.create(slug="campaign-finance-filings", name="Campaign finance",
                                  dataset="campaign-finance", ingest="documents")  # fmt: skip
    monkeypatch.setattr(source, "committees",
                        lambda fetcher, month: [{"url": URL, "cid": "1111", "rid": "2423",
                                                 "ballot_question": None,
                                                 "cfr_username": "rick"}])  # fmt: skip
    monkeypatch.setattr(
        source, "page", lambda fetcher, url, month: (FIXTURES / "committee-1111.html").read_bytes()
    )
    get("campaign-finance-filings")
    run = run_source("campaign-finance-filings", fetcher=object())
    assert (run.added, run.removed) == (1, 0) and rs.records.count() == 1
    rick = Record.objects.get(key="1111")  # the committee, whatever its latest registration
    assert rick.source_url == URL
    assert rick.documents.count() == 8 and rick.data["filings"] == 8
    assert sorted(p.short_name for p in rick.places.all()) == ["Rapid City", "South Dakota"]
    roles = set(Mention.objects.filter(record_key="campaign-finance-filings:1111")
                .values_list("role", flat=True))  # fmt: skip
    assert roles == {"committee", "candidate", "chair", "treasurer"}
