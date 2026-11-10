"""The escalation ladder, and the memory of which rung each host needs."""

from __future__ import annotations

import json
import os
from datetime import date, timedelta
from pathlib import Path

# Lightest first. http and impersonate send requests; the rest drive a real browser.
LADDER = ("http", "impersonate", "stealth", "patchright", "camoufox", "nodriver")
BROWSER_RUNGS = LADDER[2:]
RECHECK_DAYS = 7  # how often a host on a higher rung tries the one below again


def cache_dir() -> Path:
    return Path(os.environ.get("SDFETCH_CACHE") or "~/.cache/sdfetch").expanduser()


class HostMemory:
    """hosts.json: for each host, the rung that last worked and when the one below was last
    tried. Starting there saves climbing the ladder on every run; trying the rung below once
    a week moves a host back down when the site relaxes."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else cache_dir() / "hosts.json"
        self.hosts: dict[str, dict] = (
            json.loads(self.path.read_text()) if self.path.exists() else {}
        )

    def rung(self, host: str) -> str:
        return self.hosts.get(host, {}).get("rung", "http")

    def start(self, host: str, today: date | None = None) -> str:
        """The rung to begin with: the remembered one, or the one below it once a week."""
        today = today or date.today()
        entry = self.hosts.get(host)
        if not entry or entry["rung"] == "http":
            return "http"
        checked = date.fromisoformat(entry.get("checked_lower") or entry["since"])
        if today - checked >= timedelta(days=RECHECK_DAYS):
            entry["checked_lower"] = today.isoformat()
            self._save()
            return LADDER[LADDER.index(entry["rung"]) - 1]
        return entry["rung"]

    def record(self, host: str, rung: str, today: date | None = None) -> None:
        today = today or date.today()
        entry = self.hosts.get(host)
        if entry and entry["rung"] == rung:
            return
        self.hosts[host] = {"rung": rung, "since": today.isoformat()}
        self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".partial")
        tmp.write_text(json.dumps(self.hosts, indent=1, sort_keys=True))
        tmp.replace(self.path)
