import json
import re

import pytest
import requests

import sdfetch
from sdfetch import wayback
from sdfetch._browser import BrowserResponse

URL = "https://records.example.gov/budget"


class Site:
    """A page whose answer the test sets: content, a status, a redirect or a failure."""

    def __init__(self):
        self.status, self.body, self.final, self.fail = 200, b"<p>v1</p>", URL, None

    def __call__(self, method, url, **kwargs):
        if self.fail:
            raise self.fail
        return BrowserResponse(self.final, self.status, self.body, {"Content-Type": "text/html"})


class Archive:
    def __call__(self, method, url, params=None, **kwargs):
        if url == wayback.CDX:
            rows = [wayback.FIELDS, ["20261101000000", URL, "text/html", "200", "D", "9"]]
            return BrowserResponse(url, 200, json.dumps(rows).encode(), {})
        return BrowserResponse(url, 200, b"<p>archived v1</p>", {"Content-Type": "text/html"})


@pytest.fixture
def site():
    return Site()


@pytest.fixture
def watch(tmp_path, site):
    f = sdfetch.Fetcher(tmp_path, strategy="http", memory=False, delay=0, retries=1,
                        clients={"http": site}, sleep=lambda s: None)  # fmt: skip
    archive = sdfetch.Fetcher(tmp_path, strategy="http", memory=False, delay=0,
                              clients={"http": Archive()}, sleep=lambda s: None)  # fmt: skip
    return sdfetch.Watch(f, archive=archive)


def test_a_pages_life(watch, site):
    assert watch.check(URL)["kind"] == "new"
    assert watch.check(URL)["kind"] == "unchanged"
    site.body = b"<p>v2</p>"
    changed = watch.check(URL)
    assert changed["kind"] == "changed" and changed["previous_blob"] != changed["blob"]
    site.status, site.body = 404, b"not found"
    gone = watch.check(URL)
    assert gone["kind"] == "gone" and gone["last_good"] == changed["blob"]
    assert gone["archived"] == f"https://web.archive.org/web/20261101000000/{URL}"
    assert watch.store.read(gone["archived_copy"]) == b"<p>archived v1</p>"
    assert watch.check(URL)["kind"] == "unchanged"  # still gone: not a new event
    site.status, site.body = 200, b"<p>v3</p>"
    assert watch.check(URL)["kind"] == "back"
    site.final = "https://records.example.gov/budget-archive"
    moved = watch.check(URL)
    assert moved["kind"] == "redirected" and moved["to_url"].endswith("budget-archive")
    kinds = [e["kind"] for e in watch.events(URL)]
    assert kinds == ["new", "changed", "gone", "back", "redirected"]
    # Every version is kept, by content
    assert watch.store.read(changed["blob"]) == b"<p>v2</p>"


def test_blocked_or_down_is_not_gone(watch, site):
    watch.check(URL)
    site.status = 503
    assert watch.check(URL)["kind"] == "unreachable"
    site.status, site.fail = 200, requests.ConnectionError("refused")
    assert watch.check(URL)["kind"] == "unreachable"
    site.fail = None
    assert watch.check(URL)["kind"] == "unchanged"  # never counted as gone or back


def test_normalize_ignores_noise(tmp_path, site):
    f = sdfetch.Fetcher(tmp_path, strategy="http", memory=False, delay=0,
                        clients={"http": site}, sleep=lambda s: None)  # fmt: skip
    w = sdfetch.Watch(f, archive=False,
                      normalize=lambda b: re.sub(rb"token=\w+", b"", b))  # fmt: skip
    site.body = b"<p>v1</p><a href='?token=abc'>"
    w.check(URL)
    site.body = b"<p>v1</p><a href='?token=xyz'>"
    assert w.check(URL)["kind"] == "unchanged"


def test_state_survives_a_restart(tmp_path, site):
    def make():
        f = sdfetch.Fetcher(tmp_path, strategy="http", memory=False, delay=0,
                            clients={"http": site}, sleep=lambda s: None)  # fmt: skip
        return sdfetch.Watch(f, archive=False)

    make().check(URL)
    assert make().check(URL)["kind"] == "unchanged"
