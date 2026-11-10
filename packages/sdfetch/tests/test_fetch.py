import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests

import sdfetch

CHALLENGE = b"<html><head><title>Just a moment...</title></head><body>cf-chl</body></html>"


class Handler(BaseHTTPRequestHandler):
    hits: dict[str, int] = {}

    def log_message(self, *args):
        pass

    def _send(self, status, body=b"", headers=None):
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        n = Handler.hits[self.path] = Handler.hits.get(self.path, 0) + 1
        if self.path == "/ok":
            self._send(200, f"page {n}".encode())
        elif self.path == "/empty":
            self._send(200, b"")
        elif self.path == "/challenge":
            self._send(403, CHALLENGE)
        elif self.path == "/flaky":  # fails twice, then answers
            if n <= 2:
                self._send(503, b"busy", {"Retry-After": "7"})
            else:
                self._send(200, b"finally")
        elif self.path == "/sorry":
            self._send(200, b"<p>We're sorry, the service is temporarily unavailable</p>")
        elif self.path == "/ua":
            self._send(200, self.headers["User-Agent"].encode())
        else:
            self._send(404, b"no")

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        self._send(200, b"posted " + body)


@pytest.fixture(scope="module")
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture
def waits():
    return []


@pytest.fixture
def fetcher(tmp_path, waits):
    Handler.hits = {}
    return sdfetch.Fetcher(sdfetch.Store(tmp_path), strategy="http", delay=0, sleep=waits.append)


def test_fetch_stores_and_reuses(fetcher, server, tmp_path):
    assert fetcher.fetch(f"{server}/ok", "pages/ok.html") == b"page 1"
    assert fetcher.fetch(f"{server}/ok", "pages/ok.html") == b"page 1"  # from the store
    assert Handler.hits["/ok"] == 1
    assert (tmp_path / "pages" / "ok.html").read_bytes() == b"page 1"
    assert not list(tmp_path.rglob("*.partial"))
    [entry] = fetcher.store.manifest()
    assert entry["url"] == f"{server}/ok"
    assert (entry["path"], entry["method"], entry["status"], entry["bytes"], entry["via"]) == (
        "pages/ok.html",
        "GET",
        200,
        6,
        "http",
    )
    assert fetcher.fetch(f"{server}/ok", "pages/ok.html", refresh=True) == b"page 2"
    assert len(fetcher.store.manifest()) == 2


def test_post_and_note(fetcher, server):
    body = fetcher.fetch(f"{server}/x", "exports/a.xlsx", data={"a": "1"}, note={"label": "A"})
    assert body == b"posted a=1"
    [entry] = fetcher.store.manifest()
    assert (entry["method"], entry["label"]) == ("POST", "A")


def test_refresh_never_replaces_good_data_with_empty(fetcher, server, tmp_path):
    fetcher.store.save("pages/p.html", b"good", "earlier")
    with pytest.raises(sdfetch.EmptyResponse):
        fetcher.fetch(f"{server}/empty", "pages/p.html", refresh=True)
    assert (tmp_path / "pages" / "p.html").read_bytes() == b"good"
    assert fetcher.fetch(f"{server}/empty", "pages/new.html") == b""  # nothing to protect


def test_retries_honor_retry_after(fetcher, server, waits):
    assert fetcher.request("GET", f"{server}/flaky").content == b"finally"
    assert Handler.hits["/flaky"] == 3
    assert waits == [7.0, 7.0]


def test_gives_up_with_http_error(tmp_path, server, waits):
    Handler.hits = {}
    f = sdfetch.Fetcher(tmp_path, delay=0, retries=2, sleep=waits.append)
    with pytest.raises(requests.HTTPError):
        f.request("GET", f"{server}/flaky")
    with pytest.raises(requests.HTTPError):
        f.request("GET", f"{server}/missing")
    assert Handler.hits["/missing"] == 1  # a 404 isn't retried


def test_block_pages(fetcher, server, waits, tmp_path):
    with pytest.raises(sdfetch.Blocked) as e:
        fetcher.fetch(f"{server}/challenge", "pages/c.html")
    assert e.value.reason == "Cloudflare challenge (http)"
    assert Handler.hits["/challenge"] == 4
    assert waits == [20, 40, 80]  # four times the usual backoff
    assert not (tmp_path / "pages" / "c.html").exists()
    # Per-source phrases, like the SOS server's "we're sorry" page
    f = sdfetch.Fetcher(
        tmp_path, strategy="http", delay=0, retries=1, block_phrases=["we're sorry"]
    )
    with pytest.raises(sdfetch.Blocked):
        f.request("GET", f"{server}/sorry")
    assert fetcher.request("GET", f"{server}/sorry").ok  # not a block page by default


def test_pause_between_requests_to_a_host(tmp_path, server, waits):
    f = sdfetch.Fetcher(tmp_path, delay=30, sleep=waits.append)
    f.request("GET", f"{server}/ok")
    f.request("GET", f"{server}/ok")
    assert len(waits) == 1 and 29 < waits[0] <= 30
    other = sdfetch.Fetcher(tmp_path, delays={"127.0.0.1": 0}, sleep=waits.append)
    other.request("GET", f"{server}/ok")
    assert len(waits) == 2  # still waits for the other Fetcher's slot


def test_requests_look_like_a_browser(tmp_path, server):
    f = sdfetch.Fetcher(tmp_path, delay=0)
    ua = f.request("GET", f"{server}/ua").text
    assert ua == sdfetch.USER_AGENT and ua.startswith("Mozilla/5.0 (Macintosh")
    assert f"Chrome/{sdfetch.CHROME}.0.0.0" in ua
    assert f.session.headers["sec-ch-ua-platform"] == '"macOS"'
    for leak in ("archive", "sdfetch", "south-dakota", "python", "bot", "github"):
        assert leak not in " ".join(f.session.headers.values()).lower()
    f = sdfetch.Fetcher(tmp_path, delay=0, user_agent="Mozilla/5.0 (other)")
    assert f.request("GET", f"{server}/ua").text == "Mozilla/5.0 (other)"


def test_blob(tmp_path):
    store = sdfetch.Store(tmp_path)
    name = store.blob(b"%PDF-1.7", "https://example.org/a.pdf", suffix=".pdf")
    assert name.startswith("blobs/") and name.endswith(".pdf")
    assert store.blob(b"%PDF-1.7", "https://example.org/b.pdf", suffix=".pdf") == name
    assert [e["url"] for e in store.manifest()] == [
        "https://example.org/a.pdf",
        "https://example.org/b.pdf",
    ]


def test_store_names_stay_inside(tmp_path):
    store = sdfetch.Store(tmp_path)
    for bad in ("../x", "/etc/passwd", ""):
        with pytest.raises(ValueError):
            store.path(bad)


def test_checkpoint(tmp_path):
    path = tmp_path / "cp" / "checkpoint.json"
    cp = sdfetch.checkpoint(path)
    assert not cp.done(1) and cp.cursor is None
    cp.mark(1, rows=12)
    cp.cursor = "page-3"
    again = sdfetch.checkpoint(path)  # as after an interruption
    assert again.done(1) and again.done("1") and not again.done(2)
    assert again.info(1)["rows"] == 12 and again.cursor == "page-3"
    again.unmark(1)
    assert not sdfetch.checkpoint(path).done(1)
    assert json.loads(path.read_text())["cursor"] == "page-3"


def test_disk_guard(tmp_path):
    assert sdfetch.ensure_free(tmp_path / "not" / "yet", gb=0) > 0
    with pytest.raises(sdfetch.DiskSpaceLow, match="GB free"):
        sdfetch.ensure_free(tmp_path, gb=10**9)


def test_raise_errors_false_returns_the_response(tmp_path, server, waits):
    Handler.hits = {}
    f = sdfetch.Fetcher(tmp_path, strategy="http", delay=0, retries=2, raise_errors=False,
                        sleep=waits.append)  # fmt: skip
    assert f.request("GET", f"{server}/missing").status_code == 404
    assert f.request("GET", f"{server}/flaky").status_code == 503  # after its retries
    assert Handler.hits["/flaky"] == 2
    with pytest.raises(requests.HTTPError):  # fetch() never stores an error page
        f.fetch(f"{server}/missing", "missing.html")
    assert not f.store.has("missing.html")
    with pytest.raises(requests.HTTPError):
        f.request("GET", f"{server}/missing", raise_errors=True)
