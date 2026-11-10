import gzip
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from warcio.archiveiterator import ArchiveIterator

import sdfetch
from sdfetch._browser import BrowserResponse


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, body, headers=None):
        self.send_response(200)
        for k, v in {"Content-Type": "text/html; charset=utf-8", **(headers or {})}.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/gz":
            return self._send(gzip.compress(b"<p>squeezed</p>"), {"Content-Encoding": "gzip"})
        self._send(b"<p>plain</p>")

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        self._send(b"posted " + body)


@pytest.fixture(scope="module")
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def records(path):
    with path.open("rb") as f:
        out = []
        for r in ArchiveIterator(f, check_digests="raise"):
            out.append(
                (
                    r.rec_type,
                    r.rec_headers.get_header("WARC-Target-URI"),
                    r.rec_headers.get_header("sdfetch-via"),
                    r.http_headers.get_statuscode()
                    if r.http_headers and r.rec_type == "response"
                    else None,  # fmt: skip
                    r.content_stream().read(),
                    r.http_headers,
                )
            )
        return out


def test_warc_records_responses_and_requests(tmp_path, server):
    with sdfetch.Fetcher(tmp_path, strategy="http", delay=0, memory=False, warc=True) as f:
        f.fetch(f"{server}/a", "a.html")
        f.fetch(f"{server}/gz", "gz.html")
        f.fetch(f"{server}/form", "form.html", data={"q": "smith"})
    [path] = (tmp_path / "warc").glob("*.warc.gz")
    recs = records(path)
    assert [r[0] for r in recs] == ["warcinfo"] + ["response", "request"] * 3
    a, gz, post = recs[1], recs[3], recs[5]
    assert (a[1], a[2], a[3], a[4]) == (f"{server}/a", "http", "200", b"<p>plain</p>")
    # Stored decoded: no Content-Encoding, and a Content-Length that matches
    assert gz[4] == b"<p>squeezed</p>"
    assert gz[5].get_header("Content-Encoding") is None
    assert gz[5].get_header("Content-Length") == str(len(b"<p>squeezed</p>"))
    assert post[4] == b"posted q=smith"
    request = recs[6]
    assert request[5].protocol == "POST" or request[5].statusline.startswith("POST /form")
    assert request[4] == b"q=smith"
    assert "Chrome/" in request[5].get_header("User-Agent")


def test_manifest_points_at_the_record(tmp_path, server):
    with sdfetch.Fetcher(tmp_path, strategy="http", delay=0, memory=False, warc=True) as f:
        f.fetch(f"{server}/a", "a.html")
        f.fetch(f"{server}/b", "b.html")
    entry = f.store.manifest()[1]
    assert entry["warc"].startswith("warc/") and entry["via"] == "http"
    with (tmp_path / entry["warc"]).open("rb") as fh:
        fh.seek(entry["warc_offset"])
        record = next(iter(ArchiveIterator(fh)))
    assert record.rec_headers.get_header("WARC-Target-URI") == f"{server}/b"


def test_impersonate_and_browser_records(tmp_path, server):
    page = BrowserResponse(
        f"{server}/js", 200, b"<html>rendered</html>", {"content-type": "text/html"}
    )
    with sdfetch.Fetcher(tmp_path, strategy="impersonate", delay=0, memory=False, warc=True) as f:
        f.fetch(f"{server}/a", "a.html")
        f.strategy = "stealth"
        f.clients["stealth"] = lambda method, url, **kw: page
        f.fetch(f"{server}/js", "js.html")
    [path] = (tmp_path / "warc").glob("*.warc.gz")
    recs = records(path)
    assert [(r[0], r[2]) for r in recs[1:]] == [
        ("response", "impersonate"),
        ("request", "impersonate"),
        ("resource", "stealth"),
    ]
    assert recs[3][4] == b"<html>rendered</html>"


def test_warc_needs_a_store_or_a_folder(tmp_path):
    with pytest.raises(ValueError, match="needs a store"):
        sdfetch.Fetcher(warc=True)
    assert sdfetch.Fetcher(warc=tmp_path / "w", memory=False).warc_dir == tmp_path / "w"


def test_har_requests(tmp_path):
    har = {
        "log": {
            "entries": [
                {"request": {"method": "GET", "url": "https://x.gov/search"},
                 "response": {"status": 200, "content": {"mimeType": "text/html", "size": 900}},
                 "_resourceType": "document"},
                {"request": {"method": "POST", "url": "https://x.gov/api/rows",
                             "postData": {"text": '{"page": 2}'}},
                 "response": {"status": 200, "content": {"mimeType": "application/json"}},
                 "_resourceType": "fetch"},
                {"request": {"method": "GET", "url": "https://x.gov/app.js"},
                 "response": {"status": 200, "content": {"mimeType": "application/javascript"}}},
                {"request": {"method": "GET", "url": "https://x.gov/export.csv"},
                 "response": {"status": 200, "content": {"mimeType": "text/csv"}}},
            ]
        }
    }  # fmt: skip
    path = tmp_path / "x.har"
    path.write_text(json.dumps(har))
    found = sdfetch.har_requests(path)
    assert [(r["method"], r["url"]) for r in found] == [
        ("POST", "https://x.gov/api/rows"),
        ("GET", "https://x.gov/export.csv"),
    ]
    assert found[0]["post"] == '{"page": 2}'
    assert len(sdfetch.har_requests(path, all=True)) == 4
