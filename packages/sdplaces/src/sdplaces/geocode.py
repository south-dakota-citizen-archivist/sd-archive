"""Geocoding street addresses, with every answer cached so each address is looked up once.

Moved from whats-up-in-spearfish (scrapers/geo.py) and made usable anywhere in the state.
Needs the `geocode` extra.

    from sdplaces.geocode import Geocoder, JsonCache

    g = Geocoder(city="Spearfish", zip="57783", within=SPEARFISH_BOX,
                 cache=JsonCache(data / "_geocode_cache.json"),
                 osm_cache=JsonCache(data / "_geocode_osm.json"))
    g.batch(["2312 Platinum Drive", "440 Heritage Dr"])   # {key: [lat, lon] or None}
    g.locate_streets(keys_the_census_missed)                # {key: [lat, lon, "house"|"street"]}

1. batch(): the free US Census batch geocoder, up to 2,000 addresses a request. Misses are
   cached too (as None), so they aren't asked again.
2. locate_streets(): for what the Census misses, mostly houses on streets too new for its
   map. OpenStreetMap's Nominatim often has the new street if not the house number. Answers
   are [lat, lon, "house"] (the house itself) or [lat, lon, "street"] (somewhere on the
   street: approximate). Nominatim allows one request a second; a miss is retried after
   MISS_RETRY_DAYS, since new streets reach OpenStreetMap eventually.

A Geocoder covers one area:
- city/zip: the default for addresses that don't name one. With a fixed city, cache keys are
  the bare address key ("2312 PLATINUM DR"), matching Spearfish's existing cache files. With
  city=None, each address's own city is used and the cache key includes it
  ("100 MAIN ST, LEAD"), so the same street in two towns doesn't collide.
- within: results outside it are treated as bad data. A Box, or any (lat, lon) → bool.
  Defaults to South Dakota's bounding box.

Planned (CONSOLIDATION-PLAN Stage 4): a local lookup against county E911 address points,
tried before the Census.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import requests

from sdfetch import BROWSER_HEADERS

from . import address

logger = logging.getLogger(__name__)

CENSUS_BATCH = "https://geocoding.geo.census.gov/geocoder/locations/addressbatch"
BATCH_SIZE = 2000
NOMINATIM = "https://nominatim.openstreetmap.org/search"
NOMINATIM_DELAY = 1.1  # seconds between requests (their limit is one a second)
MISS_RETRY_DAYS = 30
STREET_LEVEL = {"road", "residential", "street", "highway"}
# Requests look like a desktop browser (sdfetch). Nominatim's usage policy asks for an
# identifying User-Agent; this doesn't send one, so keep to its one-request-a-second limit.
USER_AGENT = BROWSER_HEADERS["User-Agent"]


@dataclass(frozen=True)
class Box:
    min_lat: float
    max_lat: float
    min_lon: float
    max_lon: float

    def __call__(self, lat, lon) -> bool:
        try:
            lat, lon = float(lat), float(lon)
        except (TypeError, ValueError):
            return False
        return self.min_lat <= lat <= self.max_lat and self.min_lon <= lon <= self.max_lon


SOUTH_DAKOTA = Box(42.47, 45.95, -104.06, -96.43)


class JsonCache(dict):
    """A dict saved as JSON. Loads on creation (empty if missing or unreadable)."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        try:
            super().__init__(json.loads(self.path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            super().__init__()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".partial")
        tmp.write_text(json.dumps(self, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        tmp.replace(self.path)


def _save(cache) -> None:
    if hasattr(cache, "save"):
        cache.save()


class Geocoder:
    def __init__(
        self,
        *,
        city: str | None = None,
        state: str = "SD",
        zip: str = "",  # noqa: A002 — the field's name
        within: Callable[[float, float], bool] = SOUTH_DAKOTA,
        cache: dict | None = None,
        osm_cache: dict | None = None,
        session: requests.Session | None = None,
        user_agent: str = USER_AGENT,
    ):
        self.city, self.state, self.zip = city, state, zip
        self.within = within
        self.cache = {} if cache is None else cache
        self.osm_cache = {} if osm_cache is None else osm_cache
        self.session = session or requests.Session()
        self.user_agent = user_agent

    # --- keys -------------------------------------------------------------------------------

    def key(self, text: str) -> str:
        """The cache key for an address ('' if it isn't one)."""
        parsed = address.parse(text)
        if not parsed:
            return ""
        a = parsed[0]
        if self.city:
            return a.key()
        return f"{a.key()}, {a.city}" if a.city else a.key()

    def _parts(self, key: str) -> tuple[str, str, str, str]:
        street, _, city = key.partition(", ")
        return street, city or (self.city or ""), self.state, self.zip

    # --- Census -----------------------------------------------------------------------------

    def _census_batch(self, keys: list[str]) -> dict[str, list | None]:
        buf = io.StringIO()
        w = csv.writer(buf)
        for i, k in enumerate(keys):
            w.writerow([i, *self._parts(k)])
        resp = self.session.post(
            CENSUS_BATCH,
            files={"addressFile": ("addresses.csv", buf.getvalue(), "text/csv")},
            data={"benchmark": "Public_AR_Current"},
            headers={**BROWSER_HEADERS, "User-Agent": self.user_agent},
            timeout=600,
        )
        resp.raise_for_status()
        out: dict[str, list | None] = dict.fromkeys(keys)
        for row in csv.reader(io.StringIO(resp.text)):
            if len(row) >= 6 and row[2] == "Match" and row[5]:
                lon, lat = (float(x) for x in row[5].split(","))
                if self.within(lat, lon):
                    out[keys[int(row[0])]] = [round(lat, 6), round(lon, 6)]
        return out

    def batch(self, addresses: Iterable[str]) -> dict[str, list | None]:
        """Address key → [lat, lon] (or None when unmatched). Only uncached keys are looked up."""
        keys = sorted({self.key(a) for a in addresses} - {""})
        todo = [k for k in keys if k not in self.cache]
        for i in range(0, len(todo), BATCH_SIZE):
            chunk = todo[i : i + BATCH_SIZE]
            logger.info(
                "geocoding %d address(es) via Census (%d/%d)", len(chunk), i + len(chunk), len(todo)
            )
            self.cache.update(self._census_batch(chunk))
            _save(self.cache)  # after each batch, so an interrupted run keeps what it got
        return {k: self.cache.get(k) for k in keys}

    # --- OpenStreetMap ----------------------------------------------------------------------

    def _nominatim(self, key: str) -> list | None:
        """[lat, lon, 'house'|'street'] for an address key, or None."""
        street, city, state, _ = self._parts(key)
        resp = self.session.get(
            NOMINATIM,
            params={
                "q": ", ".join(x for x in (street, city, state) if x),
                "format": "jsonv2",
                "addressdetails": 1,
                "limit": 1,
                "countrycodes": "us",
            },
            headers={**BROWSER_HEADERS, "User-Agent": self.user_agent},
            timeout=30,
        )
        resp.raise_for_status()
        for hit in resp.json():
            lat, lon = float(hit["lat"]), float(hit["lon"])
            if not self.within(lat, lon):
                continue
            if (hit.get("address") or {}).get("house_number"):
                return [round(lat, 6), round(lon, 6), "house"]
            if hit.get("addresstype") in STREET_LEVEL or hit.get("category") == "highway":
                return [round(lat, 6), round(lon, 6), "street"]
        return None

    def locate_streets(
        self, keys: Iterable[str], limit: int = 300, today: date | None = None
    ) -> dict:
        """OpenStreetMap positions for keys the Census couldn't place; key → [lat, lon, kind].

        Looks up at most `limit` keys a run, so a backlog can't stall a scrape.
        """
        today = today or date.today()
        stale = (today - timedelta(days=MISS_RETRY_DAYS)).isoformat()
        keys = sorted(set(keys) - {""})
        cache = self.osm_cache
        todo = [
            k for k in keys
            if k not in cache or (isinstance(cache[k], dict) and cache[k].get("miss", "") < stale)
        ][:limit]  # fmt: skip
        if todo:
            logger.info("looking up %d address(es) on OpenStreetMap", len(todo))
        for n, k in enumerate(todo):
            if n:
                time.sleep(NOMINATIM_DELAY)
            try:
                found = self._nominatim(k)
            except requests.RequestException as exc:  # tried again next run
                logger.warning("OpenStreetMap lookup failed for %r: %s", k, exc)
                break
            cache[k] = found or {"miss": today.isoformat()}
        if todo:
            _save(cache)
        return {k: cache[k] for k in keys if isinstance(cache.get(k), list)}
