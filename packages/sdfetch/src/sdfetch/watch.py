"""Watching pages: each change, redirect, disappearance and return is recorded as an event.

    from sdfetch import Fetcher, Watch

    f = Fetcher(".cache/watch")
    w = Watch(f)
    event = w.check("https://example.gov/reports")   # {"kind": "changed", …}

Events, appended to watch/events.jsonl in the store:
- new: first seen
- changed: different content (after `normalize`, if given)
- redirected: now ends up at a different URL
- gone: 404 or 410 (or the `gone_statuses` given) after being seen
- back: present again after being gone
- unreachable: blocked, a connection failure, or another error status. This isn't "gone":
  a server that's down or refusing us says nothing about the page

An unchanged page updates the state (watch/state.json) without an event. Every distinct
version is kept in the store by its SHA-256 (Store.blob), so the last good copy survives
however the page changes. When a page is gone, the Wayback Machine's latest capture is
fetched and stored too, if there is one (archive=False to skip).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime

import requests

from . import wayback
from ._http import Fetcher

EXTENSIONS = {"text/html": ".html", "application/pdf": ".pdf", "application/json": ".json",
              "text/csv": ".csv", "text/plain": ".txt"}  # fmt: skip


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Watch:
    def __init__(
        self,
        fetcher: Fetcher,
        *,
        folder: str = "watch",
        gone_statuses: tuple[int, ...] = (404, 410),
        normalize: Callable[[bytes], bytes] | None = None,
        archive: bool | Fetcher = True,
    ):
        """normalize: applied before comparing versions, to ignore parts that change on
        every load (timestamps, session tokens). archive: True for the Wayback Machine through
        sdfetch.wayback.fetcher(), a Fetcher to use for it, or False."""
        if fetcher.store is None:
            raise ValueError("Watch needs a Fetcher with a store")
        self.f, self.store = fetcher, fetcher.store
        self.folder = folder
        self.gone_statuses = set(gone_statuses)
        self.normalize = normalize or (lambda b: b)
        if archive is True:
            archive = wayback.fetcher(self.store)
        self.archive = archive or None
        self.state_path = self.store.root / folder / "state.json"
        self.events_path = self.store.root / folder / "events.jsonl"
        self._state = json.loads(self.state_path.read_text()) if self.state_path.exists() else {}

    # State and events -----------------------------------------------------

    def state(self, url: str) -> dict:
        return dict(self._state.get(url, {}))

    def events(self, url: str | None = None) -> list[dict]:
        if not self.events_path.exists():
            return []
        rows = [json.loads(line) for line in self.events_path.read_text().splitlines() if line]
        return [r for r in rows if url is None or r["url"] == url]

    def _save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_name(self.state_path.name + ".partial")
        tmp.write_text(json.dumps(self._state, indent=1, sort_keys=True))
        tmp.replace(self.state_path)

    def _event(self, url: str, kind: str, **details) -> dict:
        event = {"at": _now(), "url": url, "kind": kind, **details}
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        with self.events_path.open("a") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
        return event

    # Checking --------------------------------------------------------------

    def check(self, url: str, **kwargs) -> dict:
        """Fetch url now and compare it with what was seen before. Returns the event, or
        {"kind": "unchanged", …} (which isn't logged)."""
        prev = dict(self._state.get(url, {}))
        previous_blob = prev.get("blob")
        now = _now()
        try:
            resp = self.f.request("GET", url, raise_errors=False, **kwargs)
        except (requests.RequestException, OSError) as exc:  # Blocked is a RequestException
            prev["last_checked"] = now
            self._state[url] = prev
            self._save()
            return self._event(url, "unreachable", error=f"{type(exc).__name__}: {exc}")
        status, final = resp.status_code, str(resp.url)

        if status in self.gone_statuses:
            was_gone = bool(prev.get("gone_since"))
            prev |= {"last_checked": now, "status": status}
            if was_gone:
                self._state[url] = prev
                self._save()
                return {"at": now, "url": url, "kind": "unchanged", "status": status}
            prev["gone_since"] = now
            self._state[url] = prev
            self._save()
            details: dict = {"status": status, "last_good": prev.get("blob")}
            if self.archive is not None:
                details |= self._archived(url)
            if not prev.get("first_seen"):
                details["never_seen"] = True
            return self._event(url, "gone", **details)

        if status >= 400:
            prev["last_checked"] = now
            self._state[url] = prev
            self._save()
            return self._event(url, "unreachable", status=status)

        digest = hashlib.sha256(self.normalize(resp.content)).hexdigest()
        kind = None
        if not prev.get("first_seen"):
            kind = "new"
        elif prev.get("gone_since"):
            kind = "back"
        elif prev.get("final_url") and final != prev["final_url"]:
            kind = "redirected"
        elif digest != prev.get("digest"):
            kind = "changed"
        prev |= {"last_checked": now, "status": status, "final_url": final}
        prev.pop("gone_since", None)
        if kind is None:
            self._state[url] = prev
            self._save()
            return {"at": now, "url": url, "kind": "unchanged", "status": status}
        kind_of = resp.headers.get("Content-Type") or resp.headers.get("content-type") or ""
        blob = self.store.blob(
            resp.content,
            url,
            suffix=EXTENSIONS.get(kind_of.split(";")[0].strip(), ""),
            status=status,
            note={"via": self.f.last_via, "watch": kind, "final_url": final},
        )
        details = {"status": status, "blob": blob, "digest": digest}
        if kind == "redirected":
            details["from_url"], details["to_url"] = prev.get("final_url"), final
        if kind in ("changed", "back"):
            details["previous_blob"] = previous_blob
        prev |= {"digest": digest, "blob": blob, "first_seen": prev.get("first_seen") or now}
        self._state[url] = prev
        self._save()
        return self._event(url, kind, **details)

    def _archived(self, url: str) -> dict:
        """The Wayback Machine's latest capture of url, stored, or why there isn't one."""
        try:
            snap = wayback.latest(url, fetcher=self.archive)
            if snap is None:
                return {"archived": None}
            wayback.fetch_snapshot(snap, self.archive)
            return {"archived": snap.url, "archived_copy": snap.name()}
        except (requests.RequestException, OSError, ValueError) as exc:
            return {"archived_error": f"{type(exc).__name__}: {exc}"}
