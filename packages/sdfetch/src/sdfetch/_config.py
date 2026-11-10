"""Local per-site settings, kept outside any repository.

~/.config/sdfetch/hosts.toml, or the file named by $SDFETCH_CONFIG:

    [routes]                       # named ways out; "direct" is always defined
    vpn = "socks5://127.0.0.1:1080"
    office = "http://user:pass@proxy.example.org:3128"

    [defaults]                     # every host
    routes = ["direct"]

    [hosts."example.gov"]          # this host and its subdomains
    strategy = "auto"              # or a strategy name, to pin it
    max_strategy = "patchright"
    routes = ["direct", "vpn"]
    delay = 3.0
    headers = { Referer = "https://example.gov/" }
    block_phrases = ["temporarily unavailable"]
    jitter = 0.4                   # pauses vary from 60% to 140% of delay
    hours = "08:00-18:00"          # only make requests in these hours…
    days = "mon-fri"               # …on these days…
    tz = "America/Chicago"         # …in this time zone (default: this machine's)
    budget = 400                   # at most this many requests a day

For a matching host these override the Fetcher's own arguments. The most specific host entry
wins. Route URLs can hold credentials, so the manifest records route names, never URLs; keep
the file readable only by you (chmod 600).
"""

from __future__ import annotations

import logging
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from ._ladder import DIRECT

logger = logging.getLogger(__name__)

HOST_KEYS = {
    "strategy", "max_strategy", "routes", "delay", "headers", "block_phrases",
    "jitter", "hours", "days", "tz", "budget",
}  # fmt: skip


def config_path() -> Path:
    return Path(os.environ.get("SDFETCH_CONFIG") or "~/.config/sdfetch/hosts.toml").expanduser()


def route_label(url: str) -> str:
    """A name for a route URL without its credentials: socks5://host:1080."""
    parts = urlsplit(url)
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.scheme}://{parts.hostname}{port}"


@dataclass
class Config:
    routes: dict[str, str] = field(default_factory=dict)
    defaults: dict = field(default_factory=dict)
    hosts: dict[str, dict] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> Config:
        routes = dict(data.get("routes", {}))
        defaults = dict(data.get("defaults", {}))
        hosts = {k.lower(): dict(v) for k, v in data.get("hosts", {}).items()}
        for where, entry in [("defaults", defaults), *hosts.items()]:
            if unknown := set(entry) - HOST_KEYS:
                raise ValueError(f"sdfetch config [{where}]: unknown keys {sorted(unknown)}")
            for name in entry.get("routes", []):
                if name != DIRECT and name not in routes and "://" not in name:
                    raise ValueError(f"sdfetch config [{where}]: no route named {name!r}")
        return cls(routes, defaults, hosts)

    def for_host(self, host: str) -> dict:
        """The defaults, overridden by the most specific entry matching host."""
        host = (host or "").lower()
        matches = [k for k in self.hosts if host == k or host.endswith("." + k)]
        out = dict(self.defaults)
        if matches:
            out |= self.hosts[max(matches, key=len)]
        return out

    def resolve_route(self, route: str) -> tuple[str, str | None]:
        """(name, proxy URL or None for direct) for a route name or URL."""
        if route == DIRECT:
            return DIRECT, None
        if route in self.routes:
            url = self.routes[route]
            return route, (None if url == DIRECT else url)
        if "://" in route:
            return route_label(route), route
        raise ValueError(f"no route named {route!r}; define it under [routes]")


def load_config(value: Config | dict | str | Path | bool | None = None) -> Config:
    """None: the default file, if there is one. False: none. A dict, path or Config: that."""
    if value is False:
        return Config()
    if isinstance(value, Config):
        return value
    if isinstance(value, dict):
        return Config.from_dict(value)
    path = Path(value) if value else config_path()
    if not path.exists():
        if value:
            raise FileNotFoundError(path)
        return Config()
    if path.stat().st_mode & 0o077:
        logger.warning("%s is readable by other users; it may hold proxy credentials", path)
    with path.open("rb") as f:
        return Config.from_dict(tomllib.load(f))
