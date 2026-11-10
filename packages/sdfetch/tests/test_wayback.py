import json
from datetime import date

import pytest

from sdfetch import wayback
from sdfetch._browser import BrowserResponse

PAGE = "https://sdsos.gov/general-services/concealed-pistol-permits/pistolstatistics.aspx"
HEADER = wayback.FIELDS


def row(ts, digest="AAA", mime="text/html"):
    return [ts, PAGE, mime, "200", digest, "5120"]


class FakeArchive:
    """CDX in two pages (a resume key after the first), snapshots, and Save Page Now."""

    def __init__(self):
        self.asked = []
        self.polls = 0

    def __call__(self, method, url, params=None, data=None, headers=None, **kw):
        self.asked.append((method, url, params, data))
        if url == wayback.CDX:
            if "resumeKey" not in params:
                body = [HEADER, row("20150101000000"), row("20160101000000"), [], ["KEY1"]]
            else:
                body = [HEADER, row("20170101000000", "BBB")]
            full = url + "?" + "&".join(f"{k}={v}" for k, v in params.items())
            return BrowserResponse(
                full, 200, json.dumps(body).encode(), {"Content-Type": "text/plain"}
            )
        if url.startswith(f"{wayback.WEB}/web/"):
            return BrowserResponse(
                url, 200, b"<table>permits</table>", {"Content-Type": "text/html"}
            )
        if url == f"{wayback.WEB}/save":
            return BrowserResponse(url, 200, b'{"url": "x", "job_id": "spn2-abc"}', {})
        if url.endswith("/save/status/spn2-abc"):
            self.polls += 1
            done = {"status": "success", "timestamp": "20261110120000", "original_url": PAGE}
            state = {"status": "pending"} if self.polls < 2 else done
            return BrowserResponse(url, 200, json.dumps(state).encode(), {})
        return BrowserResponse(url, 404, b"", {})


@pytest.fixture
def archive():
    return FakeArchive()


@pytest.fixture
def f(tmp_path, archive):
    return wayback.fetcher(tmp_path, strategy="http", delay=0, memory=False,
                           clients={"http": archive}, sleep=lambda s: None)  # fmt: skip


def test_snapshots_pages_through_the_index(f, archive):
    snaps = wayback.snapshots(PAGE, fetcher=f, start=date(2015, 1, 1), collapse="digest")
    assert [s.timestamp for s in snaps] == ["20150101000000", "20160101000000", "20170101000000"]
    first, second = archive.asked[0][2], archive.asked[1][2]
    assert first["url"] == PAGE and first["from"] == "20150101" and first["collapse"] == "digest"
    assert first["filter"] == ["statuscode:200"] and first["matchType"] == "exact"
    assert second["resumeKey"] == "KEY1"
    # Each index page is kept, for auditing the list
    names = [e["path"] for e in f.store.manifest()]
    assert names == [
        f"wayback/cdx/{wayback._slug(PAGE)}-exact-0.json",
        f"wayback/cdx/{wayback._slug(PAGE)}-exact-1.json",
    ]


def test_limit_and_bad_match(f):
    assert len(wayback.snapshots(PAGE, fetcher=f, limit=2)) == 2
    with pytest.raises(ValueError):
        wayback.snapshots(PAGE, fetcher=f, match="everything")


def test_snapshot_urls_and_names():
    s = wayback.Snapshot("20190312184512", PAGE, "text/html", "200", "AAA", 5120)
    assert s.url == f"https://web.archive.org/web/20190312184512/{PAGE}"
    assert s.raw_url == f"https://web.archive.org/web/20190312184512id_/{PAGE}"
    assert s.date == date(2019, 3, 12) and s.captured_at.hour == 18
    assert s.name() == "wayback/sdsos.gov/20190312184512.html"
    pdf = wayback.Snapshot("2019", "https://sdsos.gov/x/report.PDF", "application/octet-stream")
    assert pdf.name() == "wayback/sdsos.gov/2019.pdf"


def test_fetch_snapshot_stores_the_original_bytes(f, archive):
    snap = wayback.snapshots(PAGE, fetcher=f)[0]
    body = wayback.fetch_snapshot(snap, f)
    assert body == b"<table>permits</table>"
    assert archive.asked[-1][1] == snap.raw_url  # the id_ form: no toolbar, no rewriting
    entry = f.store.manifest()[-1]
    assert entry["path"] == "wayback/sdsos.gov/20150101000000.html"
    assert (entry["wayback_timestamp"], entry["original_url"], entry["wayback_url"]) == (
        "20150101000000", PAGE, snap.url)  # fmt: skip
    n = len(archive.asked)
    wayback.fetch_snapshot(snap, f)  # captures never change: from the store
    assert len(archive.asked) == n


def test_save_page(f, archive, monkeypatch):
    monkeypatch.setenv("IA_ACCESS_KEY", "k")
    monkeypatch.setenv("IA_SECRET_KEY", "s")
    snap = wayback.save_page(PAGE, fetcher=f, poll=0)
    assert snap.timestamp == "20261110120000" and snap.original == PAGE
    method, url, _, data = archive.asked[0]
    assert (method, url, data) == ("POST", f"{wayback.WEB}/save", {"url": PAGE})
    assert archive.polls == 2
    assert wayback.save_page(PAGE, fetcher=f, wait=False) == "spn2-abc"


def test_wayback_fetcher_defaults(tmp_path):
    f = wayback.fetcher(tmp_path, memory=False)
    assert f.delays["web.archive.org"] == 3.0 and f.max_strategy == "impersonate"


@pytest.mark.vcr  # the Wayback index's real answer, recorded
def test_real_index():
    snaps = wayback.snapshots(PAGE, start="2019", end="2020", limit=5)  # captures begin in 2019
    assert snaps and all(s.original.startswith("http") and s.status == "200" for s in snaps)
    assert snaps == sorted(snaps, key=lambda s: s.timestamp)
