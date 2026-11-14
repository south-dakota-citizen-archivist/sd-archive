"""The Secretary of State's document searches (records.sources._sos_search), through the
raffle requests: real results rows and a real viewer page, and pages made here."""

import io
from pathlib import Path

import pytest

from records.sources import _sos_search as sos
from records.sources.raffle_requests import source

FIXTURES = Path(__file__).parent / "fixtures"
FORM = (b'<html><body><form method="post" action="./Default.aspx">'
        b'<input type="hidden" name="__VIEWSTATE" value="vs"/>'
        b'<input type="text" name="FD_A723A07D" value=""/>'
        b'<input type="submit" name="cmdSubmit" value="Search"/></form></body></html>')  # fmt: skip


@pytest.fixture(autouse=True)
def no_pause(monkeypatch):
    monkeypatch.setattr(sos, "_pace", lambda *a, **k: None)
    monkeypatch.setattr(sos, "_sleep", lambda *a, **k: None)
    monkeypatch.setattr(sos, "_failed", [0])


class Response:
    def __init__(self, content: bytes, url: str, content_type: str = "text/html"):
        self.content, self.url, self.status_code = content, url, 200
        self.headers = {"Content-Type": content_type}

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", "replace")


class FakeFetcher:
    """The form, the results, a viewer and its page images."""

    store = None

    def __init__(self, images=None, error_page=False, sorry=0):
        self.asked, self.images, self.error_page = [], images or {}, error_page
        self.sorry = sorry  # how many page image requests answer "we're sorry" first

    def request(self, method, url, data=None, **kwargs):
        self.asked.append((method, url, data))
        if self.error_page:
            return Response(b"<html>error</html>", "https://sdsos.gov/error.aspx?aspxerrorpath=/x")
        if "ShowImage.aspx" in url:
            if self.sorry:
                self.sorry -= 1
                return Response(b"<html><body>We're sorry, try again later</body></html>", url)
            return Response(self.images[url[-1]], url, "image/jpeg")
        if "Document.aspx" in url:
            return Response((FIXTURES / "viewer.html").read_bytes(), url)
        if method == "POST":
            return Response((FIXTURES / "results.html").read_bytes(), url)
        return Response(FORM, url)


def jpeg(color) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (85, 110), color).save(buf, "JPEG")
    return buf.getvalue()


def test_the_results_table():
    head, rows = sos.rows((FIXTURES / "results.html").read_bytes(), source.SPEC.url)
    assert head == ["Year", "Organization", "Receive Date", "Drawing Date"]
    first = sos.row_data(source.SPEC, rows[0])
    assert (first["year"], first["organization"]) == (2015, "Ismah Temple No. 119")
    assert (first["receive_date"], first["drawing_date"]) == ("2015-03-31", "2016-02-13")
    assert first["listed"]["Receive Date"] == "3/31/2015 12:00:00 AM"  # as listed
    assert first["doc_guid"] == "00f81620-8e2a-49e8-be95-44a2a3efdf36"
    short = sos.row_data(source.SPEC, rows[-1])  # the row leaves out its empty last cells
    assert short["drawing_date"] is None and short["organization"].startswith("Lance Armstrong")


@pytest.mark.parametrize(("text", "iso"), [
    ("9/25/2015 12:00:00 AM", "2015-09-25"), ("1/27/2025", "2025-01-27"),
    ("20260919", "2026-09-19"), ("2026-09-19", "2026-09-19"),
    ("2026919", None), ("1999", None), ("Not Filed", None), ("", None)])  # fmt: skip
def test_dates(text, iso):
    assert sos.parse_date(text) == iso


def test_a_search_posts_the_form_back():
    fetcher = FakeFetcher()
    sos.search(fetcher, source.SPEC.url, {"FD_A723A07D": "2"})
    (_, _, _), (method, _, data) = fetcher.asked
    assert method == "POST" and data == {"__VIEWSTATE": "vs", "FD_A723A07D": "2",
                                         "cmdSubmit": "Search"}  # fmt: skip
    with pytest.raises(sos.LayoutChanged):
        sos.search(fetcher, source.SPEC.url, {"FD_NOSUCH": "x"})


def test_items():
    found = source.SOURCE.items(FakeFetcher())
    assert [i.key for i in found][:1] == ["00f81620-8e2a-49e8-be95-44a2a3efdf36"]
    first = found[0]
    assert first.source_url.endswith("DocGuid=00f81620-8e2a-49e8-be95-44a2a3efdf36")
    assert first.documents[0].url == first.source_url
    assert found.checks["listed"] == {"all": len(found)}


def test_the_error_page_stops_everything():
    with pytest.raises(sos.Blocked):
        source.SOURCE.items(FakeFetcher(error_page=True))
    from records.documents import _setup_errors

    assert sos.Blocked in _setup_errors()  # a document batch stops too, not just the one


def test_a_viewer_becomes_one_pdf():
    import pdfplumber

    url = (
        "https://sdsos.gov/general-information/executive-actions/raffle-requests/Search/"
        "Document.aspx?CabId=523E2A2A&DocGuid=20261002-1616-1055-5196-1879bb67d5de"
    )
    assert sos.is_viewer(url)
    assert not sos.is_viewer("https://sdcfr.sdsos.gov/Public/Document.aspx?x=1")
    fetcher = FakeFetcher(images={"1": jpeg("white"), "2": jpeg("gray")})
    pdf = sos.assemble(fetcher, url)
    with pdfplumber.open(io.BytesIO(pdf)) as doc:
        assert len(doc.pages) == 2
    # the search page, the viewer, then the pages in order
    assert fetcher.asked[0][1].endswith("/Search/Default.aspx")
    assert [u[-7:] for _, u, _ in fetcher.asked[2:]] == ["Image=1", "Image=2"]
    sos.assemble(fetcher, url)  # the next document: the same session, no search page again
    assert sum(u.endswith("/Search/Default.aspx") for _, u, _ in fetcher.asked) == 1
    with pytest.raises(sos.Blocked):
        sos.assemble(FakeFetcher(error_page=True), url)


def test_a_document_is_tried_again_after_an_error_page(monkeypatch):
    waits = []
    monkeypatch.setattr(sos, "_sleep", waits.append)
    url = ("https://sdsos.gov/general-information/executive-actions/raffle-requests/Search/"
           "Document.aspx?CabId=523E2A2A&DocGuid=20261002-1616-1055-5196-1879bb67d5de")  # fmt: skip
    fetcher = FakeFetcher(images={"1": jpeg("white"), "2": jpeg("gray")}, sorry=1)
    assert sos.assemble(fetcher, url)[:5] == b"%PDF-"
    # after the cool-down, a fresh session: the search page, the viewer, every page again
    asked = [u.split("/")[-1][:12] for _, u, _ in fetcher.asked]
    assert asked == ["Default.aspx", "Document.asp", "ShowImage.as",
                     "Default.aspx", "Document.asp", "ShowImage.as", "ShowImage.as"]  # fmt: skip
    assert waits == [sos.COOL_DOWN[0]]
    # a document that fails through every cool-down, while the search page still loads, is
    # broken: it fails, and the batch goes on, even several in a row ...
    for _ in range(sos.FAILED_IN_A_ROW - 1):
        broken = FakeFetcher(images={"1": jpeg("white")}, sorry=10)
        with pytest.raises(sos.PageUnavailable):
            sos.assemble(broken, url)
        assert broken.asked[-1][1].endswith("/Search/Default.aspx")  # the server checked
    # ... up to FAILED_IN_A_ROW: then it's a block
    with pytest.raises(sos.Blocked):
        sos.assemble(FakeFetcher(images={"1": jpeg("white")}, sorry=10), url)
    # a search page that keeps failing is the server, not the document
    sos._failed[0] = 0
    with pytest.raises(sos.ServerDown):
        sos.assemble(FakeFetcher(error_page=True), url)
    from records.documents import _setup_errors

    assert not issubclass(sos.PageUnavailable, _setup_errors())


def test_imported_records():
    from types import SimpleNamespace

    imported = SimpleNamespace(
        key=("https://sdsos.gov/general-information/executive-actions/raffle-requests/Search/"
             "Document.aspx?CabId=523E2A2A&DocGuid=00f81620-8e2a-49e8-be95-44a2a3efdf36"),
        data={"year": "2026", "drawing_date": "2026-10-23", "organization": "Corn Palace",
              "receive_date": "2026-08-17"})  # fmt: skip
    assert source.SOURCE.legacy_key(imported) == "00f81620-8e2a-49e8-be95-44a2a3efdf36"
    assert source.SOURCE.from_legacy(imported.data) == {
        "year": 2026, "organization": "Corn Palace", "receive_date": "2026-08-17",
        "drawing_date": "2026-10-23"}  # fmt: skip


def test_a_viewer_through_the_browser(monkeypatch):
    """The window's navigations stand in for HTTP: the same document, the same order."""
    from records.sources import _sos_browser

    plain = FakeFetcher(images={"1": jpeg("white"), "2": jpeg("gray")})

    class FakePage:
        url = ""

        def goto(self, url, wait_until, timeout, referer=None):
            got = plain.request("GET", url)
            self.url = got.url
            return type("R", (), {"status": 200, "headers": got.headers,
                                  "body": lambda self: got.content})()  # fmt: skip

    window = _sos_browser.BrowserFetcher("unused")
    window.page = FakePage()
    monkeypatch.setattr(sos, "BROWSER", "headless")
    monkeypatch.setattr(sos, "_window", [window])
    url = ("https://sdsos.gov/general-information/executive-actions/raffle-requests/Search/"
           "Document.aspx?CabId=523E2A2A&DocGuid=20261002-1616-1055-5196-1879bb67d5de")  # fmt: skip
    unused = FakeFetcher()
    assert sos.assemble(unused, url)[:5] == b"%PDF-"
    assert unused.asked == []  # nothing over plain HTTP
    assert [u[-7:] for _, u, _ in plain.asked[2:]] == ["Image=1", "Image=2"]
    with pytest.raises(NotImplementedError):
        window.request("POST", url)
