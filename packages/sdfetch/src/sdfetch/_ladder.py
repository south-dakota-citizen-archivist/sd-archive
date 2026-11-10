"""Fetch strategies in order, lightest first, and the memory of what works for each host."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

DIRECT = "direct"
RECHECK_DAYS = 7  # how often a host tries one step back down


class RungUnavailable(Exception):
    """A strategy or route that can't be used here: its package isn't installed, its browser
    won't start, or its proxy can't be reached. Auto mode skips it and moves on."""


@dataclass(frozen=True)
class Strategy:
    """One way of making a request.

    send: for a registered strategy, `send(method, url, *, route, timeout, **kwargs)` returns
      a requests-like response (status_code, headers, content, url). `route` is a proxy URL,
      or None for a direct connection. Built-in strategies have send=None.
    browser: True when the strategy only navigates (GET) and returns the page as rendered.
    """

    name: str
    send: Callable | None = None
    browser: bool = False


BUILTIN = (
    Strategy("http"),
    Strategy("impersonate"),
    Strategy("stealth", browser=True),
    Strategy("patchright", browser=True),
    Strategy("camoufox", browser=True),
    Strategy("nodriver", browser=True),
)
LADDER = tuple(s.name for s in BUILTIN)  # the built-in order
BROWSER_RUNGS = tuple(s.name for s in BUILTIN if s.browser)
_strategies: list[Strategy] = list(BUILTIN)


def ladder() -> tuple[str, ...]:
    """Every strategy in order, built-in and registered."""
    return tuple(s.name for s in _strategies)


def get_strategy(name: str) -> Strategy:
    for s in _strategies:
        if s.name == name:
            return s
    raise ValueError(f"unknown strategy {name!r}; known: {ladder()}")


def register_strategy(
    name: str,
    send: Callable,
    *,
    after: str | None = None,
    before: str | None = None,
    browser: bool = False,
) -> Strategy:
    """Add a strategy to the ladder: after `after`, before `before`, or at the end.
    Registering a name again replaces it in place."""
    if name in (s.name for s in BUILTIN):
        raise ValueError(f"{name!r} is built in")
    new = Strategy(name, send, browser)
    names = ladder()
    if name in names:
        _strategies[names.index(name)] = new
        return new
    if after and before:
        raise ValueError("give after= or before=, not both")
    if after:
        i = names.index(get_strategy(after).name) + 1
    elif before:
        i = names.index(get_strategy(before).name)
    else:
        i = len(_strategies)
    _strategies.insert(i, new)
    return new


def unregister_strategy(name: str) -> None:
    if name in (s.name for s in BUILTIN):
        raise ValueError(f"{name!r} is built in")
    _strategies[:] = [s for s in _strategies if s.name != name]


def cache_dir() -> Path:
    return Path(os.environ.get("SDFETCH_CACHE") or "~/.cache/sdfetch").expanduser()


class HostMemory:
    """hosts.json: for each host, the route and strategy that last worked, and when it last
    tried a step back down. Starting there saves repeating the climb on every run; stepping
    down once a week (a strategy lower, or else the route before) finds when a site relaxes."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else cache_dir() / "hosts.json"
        self.hosts: dict[str, dict] = (
            json.loads(self.path.read_text()) if self.path.exists() else {}
        )

    def rung(self, host: str) -> str:
        return self.hosts.get(host, {}).get("rung", "http")

    def route(self, host: str) -> str:
        return self.hosts.get(host, {}).get("route", DIRECT)

    def start(
        self,
        host: str,
        rungs: list[str] | None = None,
        routes: list[str] | None = None,
        today: date | None = None,
    ) -> tuple[str, str]:
        """(route, strategy) to begin with, among the rungs and routes allowed this time."""
        rungs = rungs or list(ladder())
        routes = routes or [DIRECT]
        today = today or date.today()
        entry = self.hosts.get(host)
        if not entry or entry.get("route", DIRECT) not in routes:
            return routes[0], rungs[0]
        route = entry.get("route", DIRECT)
        # The remembered strategy, or the highest allowed one below it (a lower cap, a POST)
        order = ladder()
        remembered = order.index(entry["rung"]) if entry["rung"] in order else 0
        allowed = [r for r in rungs if order.index(r) <= remembered]
        rung = allowed[-1] if allowed else rungs[0]
        if (route, rung) == (routes[0], rungs[0]):
            return route, rung
        checked = date.fromisoformat(entry.get("checked_lower") or entry["since"])
        if today - checked >= timedelta(days=RECHECK_DAYS):
            entry["checked_lower"] = today.isoformat()
            self._save()
            if rung != rungs[0]:
                return route, rungs[rungs.index(rung) - 1]
            return routes[routes.index(route) - 1], rungs[0]
        return route, rung

    def record(self, host: str, rung: str, route: str = DIRECT, today: date | None = None) -> None:
        today = today or date.today()
        entry = self.hosts.get(host)
        if entry and entry["rung"] == rung and entry.get("route", DIRECT) == route:
            return
        self.hosts[host] = {"rung": rung, "route": route, "since": today.isoformat()}
        self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".partial")
        tmp.write_text(json.dumps(self.hosts, indent=1, sort_keys=True))
        tmp.replace(self.path)
