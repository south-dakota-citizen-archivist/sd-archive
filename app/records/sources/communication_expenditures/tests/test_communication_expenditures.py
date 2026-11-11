"""Communication expenditure statements: the portal's grid (two real pages) and an archived
year page, read and run; and the live portal against a recording.
Re-record when the site changes: just record app/records/sources/communication_expenditures"""

from pathlib import Path

import pytest

import sdfetch
from entities.models import Mention
from records.models import Record, RecordSet
from records.run import run_source
from records.sources import _sdcfr
from records.sources._tables import LayoutChanged
from records.sources.communication_expenditures import source

FIXTURES = Path(__file__).parent / "fixtures"
# the fixture is two real pages of 15; the grid's count says so
TWO_PAGES = (b"<strong>144</strong> items in <strong>15</strong> pages",
             b"<strong>20</strong> items in <strong>2</strong> pages")  # fmt: skip


def page(name: str) -> bytes:
    return (FIXTURES / f"{name}.html").read_bytes().replace(*TWO_PAGES)


class FixtureFetcher:
    store = None

    def __init__(self):
        self.posts = []

    def request(self, method, url, data=None, **kwargs):
        class Resp:
            pass

        r = Resp()
        if url == source.SEARCH:
            if method == "POST":
                self.posts.append(data)
            r.content = page("filed-page-2" if method == "POST" else "filed-page-1")
        elif "2017_" in url:
            r.content = page("2017")
        else:
            r.content = b"<html><body><p>no filings</p></body></html>"
        return r


def test_the_grid_is_read_page_by_page():
    fetcher = FixtureFetcher()
    rows = list(_sdcfr.grid(fetcher, source.SEARCH, source.GRID, source.COLUMNS))
    assert len(rows) == 20
    [post] = fetcher.posts
    assert post["__EVENTARGUMENT"] == ("FireCommand:ctl00$MainContent$rgCommExList$ctl00;Page;next")
    assert not any(k.endswith("txtPassword") for k in post), "the login box isn't posted"


def test_a_short_read_fails():
    class Short(FixtureFetcher):
        def request(self, method, url, data=None, **kwargs):
            r = super().request(method, url, data, **kwargs)
            r.content = r.content.replace(
                b"<strong>20</strong> items", b"<strong>25</strong> items"
            )
            return r

    with pytest.raises(LayoutChanged, match="read 20 rows of 25"):
        list(_sdcfr.grid(Short(), source.SEARCH, source.GRID, source.COLUMNS))


def test_paging_that_doesnt_advance_fails():
    class Stuck(FixtureFetcher):
        def request(self, method, url, data=None, **kwargs):
            r = super().request("GET", url, data, **kwargs)  # page 1 again, every time
            return r

    with pytest.raises(LayoutChanged, match="repeats page 1"):
        list(_sdcfr.grid(Stuck(), source.SEARCH, source.GRID, source.COLUMNS))


def test_the_grid_name_comes_from_its_pager():
    from records.sources._tables import document

    table = document(page("filed-page-1")).cssselect(f"table#{source.GRID}_ctl00")[0]
    assert _sdcfr._unique_id(table, source.GRID) == "ctl00$MainContent$rgCommExList"


def test_run(db, state):
    RecordSet.objects.create(slug="communication-expenditures", name="Communication expenditures",
                             dataset="communication-expenditures", ingest="documents")  # fmt: skip
    run = run_source("communication-expenditures", fetcher=FixtureFetcher())
    archived = Record.objects.filter(data__system="archive")
    assert run.added == 20 + archived.count() and archived.exists()
    first = Record.objects.get(key=f"{_sdcfr.SITE}/Document.aspx?DocumentID=24074")
    assert first.data["filer_name"].startswith("Altria Client Services")
    assert first.data["name_mentioned"] == "Initiated Measure 25"
    assert first.documents.get().url == first.key
    assert Mention.objects.filter(record_key=f"communication-expenditures:{first.key}",
                                  role="spender").exists()  # fmt: skip
    pdf = archived.first()
    assert pdf.key.startswith("2017 ") and pdf.key.endswith(".pdf") and pdf.data["year"] == 2017


def test_archive_keys_survive_folder_moves():
    old = "https://sdsos.gov/elections-voting/assets/Elephants On Patrol.pdf"
    new = "https://sdsos.gov/elections-voting/assets/Archive/Prior%20to%202026/Elephants%20On%20Patrol.pdf"
    assert (
        source.archive_key(2014, old)
        == source.archive_key(2014, new)
        == ("2014 Elephants On Patrol.pdf")
    )


@pytest.mark.vcr
def test_the_live_portal(tmp_path):
    fetcher = sdfetch.Fetcher(tmp_path, delay=0)
    first = next(_sdcfr.grid(fetcher, source.SEARCH, source.GRID, source.COLUMNS))
    assert len(first) >= len(source.COLUMNS)
