"""Supplemental campaign finance statements: the portal's list (a real first page) read and
run, and the live portal against a recording.
Re-record when the site changes: just record app/records/sources/supplemental_campaign_finance"""

from pathlib import Path

import pytest

import sdfetch
from records.models import Record, RecordSet
from records.run import run_source
from records.sources import _sdcfr
from records.sources.supplemental_campaign_finance import source

FIXTURES = Path(__file__).parent / "fixtures"
ONE_PAGE = (b"<strong>1254</strong> items in <strong>126</strong> pages",
            b"<strong>10</strong> items in <strong>1</strong> pages")  # fmt: skip


class FixtureFetcher:
    store = None

    def request(self, method, url, data=None, **kwargs):
        class Resp:
            content = (FIXTURES / "page-1.html").read_bytes().replace(*ONE_PAGE)

        return Resp()


def test_run(db, state):
    RecordSet.objects.create(slug="supplemental-campaign-finance", name="Supplemental",
                             dataset="campaign-finance", ingest="documents")  # fmt: skip
    assert run_source("supplemental-campaign-finance", fetcher=FixtureFetcher()).added == 10
    first = Record.objects.get(key=source.key("118873"))
    assert first.data == {"committee_name": "Save our South Dakota Constitution",
                          "filer_id": "1057", "document_id": "118873",
                          "date_submitted": "11/03/2026", "submitted_on": "2026-11-03",
                          "form_type": "sup"}  # fmt: skip
    assert first.documents.get().url == f"{_sdcfr.SITE}/Document.aspx?DocumentID=118873&type=sup"


def test_keys_ignore_the_form_type():
    class Old:
        key = f"{_sdcfr.SITE}/Document.aspx?DocumentID=6200&type=sf"
        data = {"document_id": "6200"}

    assert source.legacy_key(Old()) == source.key("6200")


@pytest.mark.vcr
def test_the_live_portal(tmp_path):
    first = next(_sdcfr.grid(sdfetch.Fetcher(tmp_path, delay=0), source.SEARCH, source.GRID,
                             source.COLUMNS))  # fmt: skip
    assert len(first) >= 4
