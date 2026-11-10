"""Registered strategies, routes, and the local per-site config."""

import threading
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests

import sdfetch
from sdfetch._browser import BrowserResponse

URL = "https://records.example.gov/search"
CHALLENGE = b"<html><title>Just a moment...</title>cf-chl</html>"
CONFIG = {
    "routes": {"vpn": "socks5://user:secret@10.0.0.2:1080", "office": "http://10.0.0.3:3128"},
    "defaults": {"routes": ["direct"]},
    "hosts": {
        "example.gov": {"routes": ["direct", "vpn"], "delay": 0},
        "records.example.gov": {"routes": ["direct", "vpn", "office"], "delay": 0},
    },
}


class Net:
    """Fake strategies that answer by route: blocked on some routes, dead on others."""

    def __init__(self, blocked=(), dead=(), blocked_rungs=()):
        self.blocked, self.dead, self.blocked_rungs = set(blocked), set(dead), set(blocked_rungs)
        self.calls = []

    def client(self, rung):
        def send(method, url, route=None, **kwargs):
            self.calls.append((route, rung))
            if route in self.dead:
                raise requests.ConnectionError(f"{route} unreachable")
            if route in self.blocked or rung in self.blocked_rungs:
                return BrowserResponse(url, 403, CHALLENGE, {"Content-Type": "text/html"})
            return BrowserResponse(url, 200, f"{rung} via {route}".encode(), {})

        return send


def fetcher(net, tmp_path, **kwargs):
    kwargs.setdefault("config", CONFIG)
    return sdfetch.Fetcher(
        tmp_path / "store", memory=sdfetch.HostMemory(tmp_path / "hosts.json"),
        clients={r: net.client(r) for r in sdfetch.ladder()}, delay=0, sleep=lambda s: None,
        **kwargs,
    )  # fmt: skip


def test_every_strategy_on_a_route_then_the_next_route(tmp_path):
    net = Net(blocked={"direct"})
    f = fetcher(net, tmp_path)
    assert f.fetch(URL, "search.html") == b"http via vpn"
    assert net.calls == [("direct", r) for r in sdfetch.LADDER] + [("vpn", "http")]
    entry = f.store.manifest()[0]
    assert (entry["via"], entry["route"]) == ("http", "vpn")
    assert "secret" not in str(entry)  # the route's name, never its URL
    assert f.memory.route("records.example.gov") == "vpn"
    net.calls.clear()
    fetcher(net, tmp_path).request("GET", URL)  # a later run starts on the route that worked
    assert net.calls == [("vpn", "http")]


def test_a_dead_route_is_skipped_without_climbing(tmp_path):
    net = Net(dead={"direct"})
    f = fetcher(net, tmp_path, retries=1)
    assert f.request("GET", URL).content == b"http via vpn"
    assert net.calls == [("direct", "http"), ("vpn", "http")]


def test_one_route_raises_as_before(tmp_path):
    net = Net(dead={"direct"})
    with pytest.raises(requests.ConnectionError):
        fetcher(net, tmp_path, retries=1, config=False).request("GET", URL)


def test_pinned_strategy_still_changes_route(tmp_path):
    net = Net(blocked={"direct", "vpn"})
    f = fetcher(net, tmp_path, strategy="impersonate")
    assert f.request("GET", URL).content == b"impersonate via office"
    assert net.calls == [
        ("direct", "impersonate"),
        ("vpn", "impersonate"),
        ("office", "impersonate"),
    ]


def test_weekly_step_back_to_the_route_before(tmp_path):
    memory = sdfetch.HostMemory(tmp_path / "hosts.json")
    memory.record("records.example.gov", "http", "vpn", today=date.today() - timedelta(days=8))
    routes = ["direct", "vpn"]
    assert memory.start("records.example.gov", ["http", "impersonate"], routes) == (
        "direct",
        "http",
    )
    assert memory.start("records.example.gov", ["http", "impersonate"], routes) == ("vpn", "http")


def test_registered_strategy(tmp_path):
    seen = []

    def via_relay(method, url, *, route, timeout, **kwargs):
        seen.append((method, url, route))
        return BrowserResponse(url, 200, b"from the relay", {})

    sdfetch.register_strategy("relay", via_relay, after="impersonate")
    try:
        assert sdfetch.ladder()[:3] == ("http", "impersonate", "relay")
        net = Net(blocked_rungs={"http", "impersonate"})
        clients = {r: net.client(r) for r in sdfetch.LADDER}  # no fake for relay: the real one
        f = sdfetch.Fetcher(tmp_path, memory=False, clients=clients, delay=0, config=CONFIG,
                            sleep=lambda s: None)  # fmt: skip
        assert f.request("POST", URL, data={"q": 1}).content == b"from the relay"
        assert seen == [("POST", URL, None)]  # a non-browser strategy can POST; direct route
        assert f.last_via == "relay"
        with pytest.raises(ValueError, match="built in"):
            sdfetch.register_strategy("http", via_relay)
    finally:
        sdfetch.unregister_strategy("relay")
    assert "relay" not in sdfetch.ladder()


def test_config_for_host():
    c = sdfetch.load_config(CONFIG)
    assert c.for_host("records.example.gov")["routes"] == ["direct", "vpn", "office"]
    assert c.for_host("www.example.gov")["routes"] == ["direct", "vpn"]  # subdomain
    assert c.for_host("example.org") == {"routes": ["direct"]}  # defaults only
    assert c.resolve_route("vpn") == ("vpn", "socks5://user:secret@10.0.0.2:1080")
    assert c.resolve_route("direct") == ("direct", None)
    assert c.resolve_route("socks5://u:p@h.example:9050") == (
        "socks5://h.example:9050",
        "socks5://u:p@h.example:9050",
    )
    with pytest.raises(ValueError, match="unknown keys"):
        sdfetch.load_config({"hosts": {"x.gov": {"stratgy": "http"}}})
    with pytest.raises(ValueError, match="no route named"):
        sdfetch.load_config({"hosts": {"x.gov": {"routes": ["nope"]}}})


def test_config_overrides_the_code(tmp_path):
    net = Net(blocked_rungs={"http"})
    config = {"hosts": {"example.gov": {"strategy": "impersonate", "headers": {"Referer": "r"}}}}
    f = fetcher(net, tmp_path, config=config, strategy="http")  # code pins http; config wins
    assert f.request("GET", URL).content == b"impersonate via direct"


def test_config_file(tmp_path, monkeypatch):
    path = tmp_path / "hosts.toml"
    path.write_text(
        '[routes]\nvpn = "socks5://127.0.0.1:1080"\n\n[hosts."example.gov"]\nroutes = ["vpn"]\n'
    )
    path.chmod(0o600)
    monkeypatch.setenv("SDFETCH_CONFIG", str(path))
    assert sdfetch.config_path() == path
    assert sdfetch.load_config().for_host("example.gov")["routes"] == ["vpn"]
    monkeypatch.setenv("SDFETCH_CONFIG", str(tmp_path / "missing.toml"))
    assert sdfetch.load_config().hosts == {}  # no file, no settings


class ProxyHandler(BaseHTTPRequestHandler):
    """A stand-in proxy: answers every request itself, saying what it was asked for."""

    def log_message(self, *a):
        pass

    def do_GET(self):
        body = f"<html><body>proxied {self.path}</body></html>".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="module")
def proxy():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), ProxyHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


@pytest.mark.parametrize("strategy", ["http", "impersonate"])
def test_requests_go_through_the_route(strategy, proxy, tmp_path):
    config = {"routes": {"p": proxy}, "hosts": {"records.example.org": {"routes": ["p"]}}}
    f = sdfetch.Fetcher(tmp_path, strategy=strategy, delay=0, memory=False, config=config)
    resp = f.request("GET", "http://records.example.org/a?b=1")
    assert b"proxied http://records.example.org/a?b=1" in resp.content
    assert f.last_route == "p"


@pytest.mark.browser
@pytest.mark.xfail(
    reason="fails where a system network extension intercepts browser traffic",
    strict=False,
)
def test_browser_goes_through_the_route(proxy, tmp_path):
    config = {"routes": {"p": proxy}, "hosts": {"records.example.org": {"routes": ["p"]}}}
    with sdfetch.Fetcher(tmp_path, strategy="stealth", delay=0, memory=False, config=config) as f:
        resp = f.request("GET", "http://records.example.org/page")
    assert b"proxied http://records.example.org/page" in resp.content
