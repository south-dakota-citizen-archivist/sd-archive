"""State grants and contracts: real pages (the search form, a department's results, a
summary page), read and run; the live site against a recording.
Re-record when the site changes: just record app/records/sources/grants_contracts"""

from pathlib import Path

import pytest

import sdfetch
from entities.models import Mention
from records.models import Record
from records.run import run_source
from records.sources.grants_contracts import parse, source

FIXTURES = Path(__file__).parent / "fixtures"


def page(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def test_departments_leave_out_all():
    depts = dict(parse.departments(page("contracts.html")))
    assert len(depts) == 33 and "0" not in depts
    assert depts["03 "] == "AGRICULTURE & NAT. RESOURCES"


def test_results_and_summary():
    [first, *rest] = parse.results(page("results-03.html"))
    assert len(rest) == 9
    assert first == {
        "number": "27-0300-074",
        "list_description": "Underground Storage Tank Class A & B Operator Training",
        "vendor_name": "SOUTH DAK PETROLEUM MKT ASSOC", "agency": "03",
        "agency_name": "AGRICULTURE & NAT. RESOURCES", "begin_date": "2027-01-01",
        "amount": "$50,000", "amount_value": "50000.00",
        "summary_url": "https://open.sd.gov/contractsDocShow.aspx?DocID=27-0300-074+++++",
    }  # fmt: skip
    assert parse.summary(page("summary-27-0300-074.html")) == {
        "vendor_name": "SOUTH DAK PETROLEUM MKT ASSOC", "vendor_city": "PIERRE",
        "vendor_state": "SD", "solicitation_type": "Professional Services under $50K (5-18D-17)",
        "description": "Underground Storage Tank Class A & B Operator Training",
        "document_url": "https://open.sd.gov/contracts/03/27-0300-074.pdf",
    }  # fmt: skip
    with pytest.raises(parse.LayoutChanged):
        parse.summary(b"<html><body>Not found</body></html>")


class FixtureFetcher:
    """DANR's contracts are the ten rows; every other search finds nothing."""

    store = None

    def request(self, method, url, data=None, **kwargs):
        class Resp:
            content = b"<html><body></body></html>"

        if method == "GET" and url == parse.URL:
            Resp.content = page("contracts.html")
        elif method == "POST" and data["ddl_DoA"] == "03 " and data["RadioGroup1"] == "Radio2":
            Resp.content = page("results-03.html")
        elif "contractsDocShow" in url:
            Resp.content = page("summary-27-0300-074.html")
        return Resp()


def test_run(db, state, sd_places):
    run = run_source("grants-contracts", fetcher=FixtureFetcher())
    assert run.added == 10 and run.checks["found"]["03 contract"] == 10
    record = Record.objects.get(key="03 27-0300-074")
    assert (record.data["type"], record.data["solicitation_type"]) == (
        "contract",
        "Professional Services under $50K (5-18D-17)",
    )
    assert record.documents.get().url == "https://open.sd.gov/contracts/03/27-0300-074.pdf"
    assert {p.short_name for p in record.places.all()} == {"South Dakota", "Pierre"}
    assert Mention.objects.filter(record_key="grants-contracts:03 27-0300-074",
                                  role="vendor").exists()  # fmt: skip


@pytest.mark.vcr
def test_the_live_site(tmp_path):
    fetcher = sdfetch.Fetcher(tmp_path, delay=0)
    html = fetcher.request("GET", parse.URL).content
    found = parse.results(source.search(fetcher, parse.form_fields(html), "320", "contract"))
    assert all(r["number"] and r["agency"] == "320" for r in found)
