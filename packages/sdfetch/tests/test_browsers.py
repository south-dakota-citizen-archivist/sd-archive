"""Each browser rung against a local server (slow: starts real browsers).

Run with `uv run pytest -m browser packages/sdfetch`. Camoufox needs its browser once:
`uv run python -m camoufox fetch`.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import sdfetch

pytestmark = pytest.mark.browser

# A challenge that clears itself after a second, the way an interstitial does
CHALLENGE = b"""<html><head><title>Just a moment...</title></head><body>cf-chl
<script>setTimeout(() => { document.cookie = "cleared=1"; location.reload(); }, 1000)</script>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, status, body, kind="text/html; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/page":
            cleared = "cleared=1" in (self.headers.get("Cookie") or "")
            if not cleared:
                return self._send(403, CHALLENGE)
            seen = {k: v for k, v in self.headers.items()}
            return self._send(
                200, f"<html><body><pre>{json.dumps(seen)}</pre></body></html>".encode()
            )
        if self.path == "/table":  # builds its table from an API call, as many search pages do
            return self._send(
                200,
                b"<html><body><table id=t></table><script>"
                b"fetch('/data.json').then(r => r.json()).then(d => "
                b"document.getElementById('t').textContent = d.rows)</script></body></html>",
            )
        if self.path == "/data.json":
            return self._send(200, b'{"rows": 3}', "application/json")
        self._send(404, b"no")


@pytest.fixture(scope="module")
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


@pytest.mark.parametrize("strategy", sdfetch.STRATEGIES)
def test_browser_waits_out_a_challenge(strategy, server):
    with sdfetch.browser(strategy) as b:
        resp = b.get(f"{server}/page")
        assert "cf-chl" not in resp.text, strategy
        sent = json.loads(resp.text.split("<pre>")[1].split("</pre>")[0])
        ua = sent.get("User-Agent") or sent.get("user-agent")
        assert "Headless" not in ua and ua.startswith("Mozilla/5.0"), ua
        if strategy != "nodriver":
            assert resp.status_code == 200
            data = b.get(f"{server}/data.json")
            assert json.loads(data.content) == {"rows": 3}


def test_fetcher_climbs_to_a_browser(server, tmp_path):
    """http and impersonate get the challenge page; stealth waits it out."""
    with sdfetch.Fetcher(tmp_path, delay=0, memory=tmp_path / "hosts.json") as f:
        body = f.fetch(f"{server}/page", "page.html")
    assert b"cf-chl" not in body
    assert f.store.manifest()[0]["via"] == "stealth"


@pytest.mark.parametrize("strategy", ["stealth", "patchright", "camoufox"])
def test_har_finds_the_api_behind_a_page(strategy, server, tmp_path):
    har = tmp_path / f"{strategy}.har"
    with sdfetch.browser(strategy, har=har) as b:
        b.get(f"{server}/table")
        b.page.wait_for_function("document.getElementById('t').textContent === '3'")
    found = sdfetch.har_requests(har)
    assert [r["url"] for r in found] == [f"{server}/data.json"], found


def test_fetcher_har_folder(server, tmp_path):
    with sdfetch.Fetcher(tmp_path, strategy="stealth", delay=0, memory=False, har=True) as f:
        f.fetch(f"{server}/table", "table.html")
    [har] = (tmp_path / "har").glob("*-stealth.har")
    assert any(r["url"].endswith("/data.json") for r in sdfetch.har_requests(har))


def test_nodriver_inside_a_running_event_loop(server):
    """A caller that's in asyncio already (an async job, a model client's loop) can still use
    nodriver: it runs on a loop of its own."""
    import asyncio

    async def main():
        with sdfetch.browser("nodriver") as b:
            return b.get(f"{server}/page").text

    assert "cf-chl" not in asyncio.run(main())


def test_nodriver_after_a_playwright_browser(server):
    """The ladder climbs from Playwright rungs to nodriver in one thread; a Playwright browser
    left open there leaves a loop running, which nodriver must not need."""
    with sdfetch.browser("patchright") as lower:
        lower.get(f"{server}/data.json")
        with sdfetch.browser("nodriver") as b:
            assert "cf-chl" not in b.get(f"{server}/page").text
