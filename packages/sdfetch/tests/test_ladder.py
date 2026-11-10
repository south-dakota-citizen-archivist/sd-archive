"""Escalation: climbing the ladder when blocked, and remembering the rung per host."""

from datetime import date, timedelta

import pytest

import sdfetch
from sdfetch._browser import BrowserResponse

CHALLENGE = b"<html><title>Just a moment...</title>cf-chl</html>"
URL = "https://records.example.gov/search"


class Rungs:
    """Fake rungs: each answers with a challenge or the page, and logs what it was asked."""

    def __init__(self, blocked: set[str]):
        self.blocked, self.calls = blocked, []

    def client(self, rung):
        def send(method, url, **kwargs):
            self.calls.append((rung, method))
            body = CHALLENGE if rung in self.blocked else f"content via {rung}".encode()
            status = 403 if rung in self.blocked else 200
            return BrowserResponse(url, status, body, {"Content-Type": "text/html"})

        return send

    def clients(self):
        return {rung: self.client(rung) for rung in sdfetch.LADDER}


def fetcher(rungs, tmp_path, **kwargs):
    memory = sdfetch.HostMemory(tmp_path / "hosts.json")
    return sdfetch.Fetcher(
        tmp_path / "store", clients=rungs.clients(), memory=memory, delay=0,
        sleep=lambda s: None, **kwargs,
    )  # fmt: skip


def test_climbs_until_a_rung_gets_the_content(tmp_path):
    rungs = Rungs(blocked={"http", "impersonate"})
    f = fetcher(rungs, tmp_path)
    assert f.fetch(URL, "search.html") == b"content via stealth"
    assert [r for r, _ in rungs.calls] == ["http", "impersonate", "stealth"]  # no retries
    assert f.last_via == "stealth"
    assert f.store.manifest()[0]["via"] == "stealth"
    assert f.memory.rung("records.example.gov") == "stealth"


def test_starts_on_the_remembered_rung(tmp_path):
    rungs = Rungs(blocked={"http", "impersonate"})
    fetcher(rungs, tmp_path).request("GET", URL)
    rungs.calls.clear()
    again = fetcher(rungs, tmp_path)  # a later run, reading hosts.json
    again.request("GET", URL)
    assert rungs.calls == [("stealth", "GET")]


def test_tries_the_rung_below_once_a_week(tmp_path):
    memory = sdfetch.HostMemory(tmp_path / "hosts.json")
    week_ago = date.today() - timedelta(days=8)
    memory.record("records.example.gov", "patchright", today=week_ago)
    assert memory.start("records.example.gov") == ("direct", "stealth")
    assert memory.start("records.example.gov") == ("direct", "patchright")  # once a week
    # The site relaxed: stealth works now, and becomes the remembered rung
    rungs = Rungs(blocked={"http", "impersonate"})
    memory.hosts["records.example.gov"]["checked_lower"] = week_ago.isoformat()
    memory._save()
    f = fetcher(rungs, tmp_path)
    f.request("GET", URL)
    assert rungs.calls == [("stealth", "GET")]
    assert f.memory.rung("records.example.gov") == "stealth"


def test_posts_stop_at_impersonate(tmp_path):
    rungs = Rungs(blocked={"http", "impersonate"})
    with pytest.raises(sdfetch.Blocked, match="impersonate"):
        fetcher(rungs, tmp_path).request("POST", URL, data={"q": "x"})
    assert [r for r, _ in rungs.calls] == ["http", "impersonate"]


def test_max_strategy_caps_the_climb(tmp_path):
    rungs = Rungs(blocked={"http", "impersonate"})
    with pytest.raises(sdfetch.Blocked):
        fetcher(rungs, tmp_path, max_strategy="impersonate").request("GET", URL)
    assert "stealth" not in [r for r, _ in rungs.calls]


def test_blocked_everywhere_raises_and_stores_nothing(tmp_path):
    rungs = Rungs(blocked=set(sdfetch.LADDER))
    f = fetcher(rungs, tmp_path)
    with pytest.raises(sdfetch.Blocked, match="nodriver"):
        f.fetch(URL, "search.html")
    assert [r for r, _ in rungs.calls] == list(sdfetch.LADDER)
    assert not f.store.has("search.html")
    assert f.memory.rung("records.example.gov") == "http"  # nothing worked, nothing learned


def test_pinned_strategy(tmp_path):
    rungs = Rungs(blocked=set())
    f = fetcher(rungs, tmp_path, strategy="camoufox")
    assert f.request("GET", URL).content == b"content via camoufox"
    assert rungs.calls == [("camoufox", "GET")]
    with pytest.raises(ValueError, match="only navigates"):
        f.request("POST", URL, data={})
    with pytest.raises(ValueError):
        sdfetch.Fetcher(strategy="selenium")


def test_impersonate_for_real(tmp_path):
    """curl_cffi against a local server: Chrome's own headers, nothing else."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Echo(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = self.headers["User-Agent"].encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Echo)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        f = sdfetch.Fetcher(tmp_path, strategy="impersonate", delay=0, memory=False)
        ua = f.request("GET", f"http://127.0.0.1:{httpd.server_address[1]}/").text
    finally:
        httpd.shutdown()
        httpd.server_close()
    assert ua.startswith("Mozilla/5.0 (Macintosh") and "Chrome/" in ua


def test_unavailable_rungs_are_skipped_in_auto(tmp_path):
    rungs = Rungs(blocked={"http"})

    def missing(method, url, **kwargs):
        raise sdfetch.RungUnavailable("impersonate: curl_cffi isn't installed")

    f = fetcher(rungs, tmp_path)
    f.clients["impersonate"] = missing
    assert f.request("GET", URL).content == b"content via stealth"
    pinned = fetcher(rungs, tmp_path, strategy="impersonate")
    pinned.clients["impersonate"] = missing
    with pytest.raises(sdfetch.RungUnavailable):
        pinned.request("GET", URL)


def test_blocked_is_a_requests_exception():
    import requests

    assert issubclass(sdfetch.Blocked, requests.RequestException)


def test_browser_response_like_requests():
    import requests

    ok = BrowserResponse(URL, 200, b'{"a": 1}')
    assert ok.json() == {"a": 1} and ok.ok
    ok.raise_for_status()
    with pytest.raises(requests.HTTPError):
        BrowserResponse(URL, 404, b"").raise_for_status()
