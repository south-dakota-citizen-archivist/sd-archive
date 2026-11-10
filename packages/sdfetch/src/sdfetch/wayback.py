"""The Wayback Machine: a URL's snapshots, their original bytes, and Save Page Now.

    from sdfetch import wayback

    f = wayback.fetcher(".cache/pistol-permits")
    for snap in wayback.snapshots(PAGE, fetcher=f):
        html = wayback.fetch_snapshot(snap, f)  # stored at wayback/<host>/<timestamp>.html

- snapshots(): the CDX index for a URL (or a prefix, host or domain), paged with resume keys,
  filtered by date, status and type. collapse="digest" drops captures identical to the one
  before. With a store, each CDX page is kept too (wayback/cdx/…), so the list can be audited.
- fetch_snapshot(): the bytes the site served, from the `id_` URL, without the Wayback
  toolbar or rewritten links. The manifest line names the capture time and original URL.
- save_page(): asks the Wayback Machine to capture a page now. That makes a public capture,
  so nothing here calls it on its own; a source calls it on purpose. With IA S3 keys in
  IA_ACCESS_KEY / IA_SECRET_KEY it runs as that account, which has higher limits.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import urlsplit

from ._http import Fetcher
from ._store import Store

WEB = "https://web.archive.org"
CDX = f"{WEB}/cdx/search/cdx"
FIELDS = ["timestamp", "original", "mimetype", "statuscode", "digest", "length"]
PAGE_SIZE = 5000
HOST = "web.archive.org"
EXTENSIONS = {
    "text/html": ".html",
    "application/pdf": ".pdf",
    "application/json": ".json",
    "text/csv": ".csv",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
}


class SaveFailed(Exception):
    """Save Page Now refused the URL or the capture failed."""


@dataclass(frozen=True)
class Snapshot:
    timestamp: str  # 14 digits, UTC: 20190312184512
    original: str  # the URL as captured
    mimetype: str = ""
    status: str = ""
    digest: str = ""  # SHA-1 of the content, base 32: identical captures share it
    length: int | None = None

    @property
    def captured_at(self) -> datetime:
        return datetime.strptime(self.timestamp[:14].ljust(14, "0"), "%Y%m%d%H%M%S").replace(
            tzinfo=UTC
        )

    @property
    def date(self) -> date:
        return self.captured_at.date()

    @property
    def url(self) -> str:
        """The capture as people see it, with the Wayback toolbar: cite this one."""
        return f"{WEB}/web/{self.timestamp}/{self.original}"

    @property
    def raw_url(self) -> str:
        """The bytes the site served, unchanged (the `id_` form)."""
        return f"{WEB}/web/{self.timestamp}id_/{self.original}"

    def name(self) -> str:
        """A store name: wayback/<host>/<timestamp><extension>."""
        host = urlsplit(self.original).hostname or "unknown"
        ext = EXTENSIONS.get(self.mimetype.split(";")[0].strip(), "")
        if not ext:
            m = re.search(r"\.([A-Za-z0-9]{2,5})$", urlsplit(self.original).path)
            ext = f".{m[1].lower()}" if m else ""
        return f"wayback/{host}/{self.timestamp}{ext}"


def fetcher(store: Store | str | Path | None = None, **kwargs) -> Fetcher:
    """A Fetcher suited to the Wayback Machine: a few seconds between requests (it limits
    clients that go faster), and no browsers, which it never needs."""
    kwargs.setdefault("delays", {HOST: 3.0})
    kwargs.setdefault("max_strategy", "impersonate")
    return Fetcher(store, **kwargs)


_default_fetcher = fetcher  # the functions below take a `fetcher` argument


def _slug(url: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", url).strip("-")[:120]


def snapshots(
    url: str,
    *,
    match: str = "exact",
    start: str | date | None = None,
    end: str | date | None = None,
    status: str | None = "200",
    mimetype: str | None = None,
    collapse: str | None = None,
    limit: int | None = None,
    fetcher: Fetcher | None = None,
) -> list[Snapshot]:
    """Every capture of url in the CDX index, oldest first.

    match: "exact", "prefix" (everything under a path), "host" or "domain".
    start, end: dates or timestamp prefixes ("2019", "201903", date(2019, 3, 1)).
    status: keep captures with this HTTP status ("200" by default; None for all).
    collapse: "digest" drops a capture identical to the one before it.
    """
    if match not in ("exact", "prefix", "host", "domain"):
        raise ValueError("match must be exact, prefix, host or domain")
    f = fetcher or _default_fetcher()
    params: dict = {"url": url, "matchType": match, "output": "json", "fl": ",".join(FIELDS),
                    "showResumeKey": "true", "limit": PAGE_SIZE}  # fmt: skip
    for key, value in (("from", start), ("to", end)):
        if value:
            params[key] = value.strftime("%Y%m%d") if isinstance(value, date) else str(value)
    filters = []
    if status:
        filters.append(f"statuscode:{status}")
    if mimetype:
        filters.append(f"mimetype:{mimetype}")
    if filters:
        params["filter"] = filters
    if collapse:
        params["collapse"] = collapse

    out: list[Snapshot] = []
    page = 0
    while True:
        resp = f.request("GET", CDX, params=params)
        if f.store is not None:
            name = f"wayback/cdx/{_slug(url)}-{match}-{page}.json"
            f.store.save(name, resp.content, resp.url, status=resp.status_code,
                         note={"via": f.last_via, "cdx_page": page})  # fmt: skip
        rows = json.loads(resp.content or b"[]")
        resume = None
        if rows and rows[0] == FIELDS:
            rows = rows[1:]
        # With showResumeKey, the last rows are an empty row and then [resume key]
        if len(rows) >= 2 and rows[-2] == [] and len(rows[-1]) == 1:
            resume = rows[-1][0]
            rows = rows[:-2]
        for r in rows:
            if len(r) != len(FIELDS):
                continue
            ts, original, mime, code, digest, length = r
            out.append(
                Snapshot(
                    ts, original, mime, code, digest, int(length) if length.isdigit() else None
                )
            )
            if limit and len(out) >= limit:
                return out
        if not resume:
            return out
        params["resumeKey"] = resume
        page += 1


def latest(
    url: str, *, fetcher: Fetcher | None = None, status: str | None = "200"
) -> Snapshot | None:
    """The newest capture of url, or None if there isn't one."""
    f = fetcher or _default_fetcher()
    params: dict = {"url": url, "output": "json", "fl": ",".join(FIELDS), "limit": -1}
    if status:
        params["filter"] = [f"statuscode:{status}"]
    rows = json.loads(f.request("GET", CDX, params=params).content or b"[]")
    rows = [r for r in rows if r != FIELDS and len(r) == len(FIELDS)]
    if not rows:
        return None
    ts, original, mime, code, digest, length = rows[-1]
    return Snapshot(ts, original, mime, code, digest, int(length) if length.isdigit() else None)


def fetch_snapshot(snap: Snapshot, fetcher: Fetcher, name: str | None = None, **kwargs) -> bytes:
    """The original bytes of a capture, stored at name (wayback/<host>/<timestamp>.<ext>).
    A capture never changes, so a stored one is never fetched again."""
    note = {"wayback_timestamp": snap.timestamp, "original_url": snap.original,
            "wayback_url": snap.url, **kwargs.pop("note", {})}  # fmt: skip
    return fetcher.fetch(snap.raw_url, name or snap.name(), note=note, **kwargs)


def save_page(
    url: str,
    *,
    fetcher: Fetcher | None = None,
    wait: bool = True,
    timeout: float = 300,
    poll: float = 5,
) -> Snapshot | str:
    """Ask the Wayback Machine to capture url now (Save Page Now). Returns the new Snapshot,
    or the job ID when wait=False. This publishes a capture; call it deliberately."""
    f = fetcher or _default_fetcher()
    headers = {"Accept": "application/json"}
    if (key := os.environ.get("IA_ACCESS_KEY")) and (secret := os.environ.get("IA_SECRET_KEY")):
        headers["Authorization"] = f"LOW {key}:{secret}"
    resp = f.request("POST", f"{WEB}/save", data={"url": url}, headers=headers)
    try:
        job = json.loads(resp.content)
    except ValueError as exc:
        raise SaveFailed(f"{url}: Save Page Now didn't answer with JSON") from exc
    if "job_id" not in job:
        raise SaveFailed(f"{url}: {job.get('message') or job}")
    if not wait:
        return job["job_id"]
    deadline = time.monotonic() + timeout
    while True:
        state = json.loads(
            f.request("GET", f"{WEB}/save/status/{job['job_id']}", headers=headers).content
        )
        if state.get("status") == "success":
            return Snapshot(state["timestamp"], state.get("original_url") or url)
        if state.get("status") == "error":
            raise SaveFailed(f"{url}: {state.get('message') or state.get('status_ext')}")
        if time.monotonic() > deadline:
            raise SaveFailed(
                f"{url}: still pending after {timeout:g} seconds (job {job['job_id']})"
            )
        f.sleep(poll)
