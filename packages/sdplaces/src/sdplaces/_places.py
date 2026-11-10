"""The places table and lookups."""

from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from functools import cache
from importlib import resources

from rapidfuzz import process

from ._names import compact_key, spaced_key

STATE = "46"
KINDS = ("state", "county", "place", "cdp", "tribal")


@dataclass(frozen=True)
class Place:
    kind: str
    geoid: str
    geoidfq: str  # Census fully qualified ID, unique across kinds
    name: str  # official name: "Lawrence County", "Spearfish city"
    short_name: str  # how records usually write it: "Lawrence", "Spearfish"
    slug: str
    parent: str  # GEOID of the county (cities, CDPs) or state (counties, tribal areas)
    lat: float | None
    lon: float | None
    ocd_id: str
    valid_from: date | None
    valid_to: date | None  # the first day it no longer existed under this code
    successor: str  # GEOID that replaced it, if any

    def in_force(self, on: date) -> bool:
        return (self.valid_from is None or self.valid_from <= on) and (
            self.valid_to is None or on < self.valid_to
        )

    @property
    def historical(self) -> bool:
        return self.valid_to is not None

    def __str__(self) -> str:
        return self.name


class UnknownPlace(LookupError):
    """No place matches. `suggestions` lists near misses; nothing is matched automatically."""

    def __init__(self, message: str, suggestions: list[str] | None = None):
        self.suggestions = suggestions or []
        hint = f" Did you mean: {', '.join(self.suggestions)}?" if self.suggestions else ""
        super().__init__(message + hint)


class AmbiguousPlace(UnknownPlace):
    """Several places match. `candidates` lists them; narrow with within= or as_of=."""

    def __init__(self, message: str, candidates: list[Place]):
        self.candidates = candidates
        names = "; ".join(f"{p.name} ({p.geoid}, in {p.parent})" for p in candidates)
        super().__init__(f"{message}: {names}")


class NotInForce(UnknownPlace):
    """The name matches a place that didn't exist under that code on the date asked about."""


def _date(value: str) -> date | None:
    return date.fromisoformat(value) if value else None


def _float(value: str) -> float | None:
    return float(value) if value else None


@cache
def _table() -> tuple[Place, ...]:
    with resources.files("sdplaces").joinpath("data/places.csv").open(newline="") as f:
        return tuple(
            Place(
                kind=r["kind"],
                geoid=r["geoid"],
                geoidfq=r["geoidfq"],
                name=r["name"],
                short_name=r["short_name"],
                slug=r["slug"],
                parent=r["parent"],
                lat=_float(r["lat"]),
                lon=_float(r["lon"]),
                ocd_id=r["ocd_id"],
                valid_from=_date(r["valid_from"]),
                valid_to=_date(r["valid_to"]),
                successor=r["successor"],
            )
            for r in csv.DictReader(f)
        )


@cache
def _aliases() -> tuple[tuple[str, str, str], ...]:
    path = resources.files("sdplaces").joinpath("data/aliases.csv")
    if not path.is_file():
        return ()
    with path.open(newline="") as f:
        return tuple((r["kind"], r["geoid"], r["alias"]) for r in csv.DictReader(f))


@cache
def _by_key() -> tuple[dict, dict, dict]:
    by_geoid: dict[tuple[str, str], Place] = {}
    spaced: dict[tuple[str, str], set[str]] = defaultdict(set)
    compact: dict[tuple[str, str], set[str]] = defaultdict(set)
    for p in _table():
        by_geoid[(p.kind, p.geoid)] = p
        for n in (p.name, p.short_name):
            spaced[(p.kind, spaced_key(n, p.kind))].add(p.geoid)
            compact[(p.kind, compact_key(n, p.kind))].add(p.geoid)
    for kind, geoid, alias in _aliases():
        spaced[(kind, spaced_key(alias, kind))].add(geoid)
        compact[(kind, compact_key(alias, kind))].add(geoid)
    return by_geoid, spaced, compact


def all_places(kind: str | None = None, *, include_historical: bool = True) -> list[Place]:
    return [
        p
        for p in _table()
        if (kind is None or p.kind == kind) and (include_historical or not p.historical)
    ]


def get(geoid: str, kind: str | None = None) -> Place:
    """A place by GEOID. GEOIDs are unique within a kind; pass kind= if one is shared."""
    geoid = str(geoid).strip()
    by_geoid = _by_key()[0]
    found = [p for (k, g), p in by_geoid.items() if g == geoid and (kind is None or k == kind)]
    if not found:
        raise UnknownPlace(f"No place with GEOID {geoid!r}" + (f" ({kind})" if kind else ""))
    if len(found) > 1:
        raise AmbiguousPlace(f"GEOID {geoid!r} is used by several kinds; pass kind=", found)
    return found[0]


def current(place: Place) -> Place:
    """Follow successors to the place in force today (Shannon County → Oglala Lakota County)."""
    seen = set()
    while place.successor and place.geoid not in seen:
        seen.add(place.geoid)
        place = get(place.successor, place.kind)
    return place


def _within(p: Place, within: str | Place | None) -> bool:
    if within is None:
        return True
    target = within.geoid if isinstance(within, Place) else str(within)
    if not target.isdigit():
        target = lookup("county", target).geoid
    return p.parent == target or p.geoid == target


def lookup(
    kind: str,
    name: str,
    *,
    as_of: date | str | None = None,
    current: bool = False,
    within: str | Place | None = None,
) -> Place:
    """The one place of this kind with this name (or an alias of it).

    Matching is exact on a normalized name: case, punctuation, "County"/"city" and, when
    unambiguous, spacing don't matter. Unknown names raise UnknownPlace with suggestions;
    names that fit several places raise AmbiguousPlace. Nothing is guessed.

    as_of: only places in force on that date (a 2010 record's "Shannon" is Shannon County).
    current: follow successors to today's place (Shannon → Oglala Lakota County).
    within: a county (GEOID, name or Place) the place must be in, to tell apart same-named places.
    """
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    if not str(name or "").strip():
        raise UnknownPlace(f"Empty {kind} name")
    on = date.fromisoformat(as_of) if isinstance(as_of, str) else as_of
    by_geoid, spaced, compact = _by_key()

    for index, keyfn in ((spaced, spaced_key), (compact, compact_key)):
        geoids = index.get((kind, keyfn(name, kind)), set())
        matches = [by_geoid[(kind, g)] for g in sorted(geoids)]
        matches = [p for p in matches if _within(p, within)]
        if not matches:
            continue
        if on is not None:
            in_force = [p for p in matches if p.in_force(on)]
            if not in_force:
                ranges = ", ".join(
                    f"{p.name}: {p.valid_from or '…'} to {p.valid_to or 'now'}" for p in matches
                )
                raise NotInForce(f"{name!r} didn't exist as a {kind} on {on} ({ranges})")
            matches = in_force
        if len(matches) > 1:
            raise AmbiguousPlace(f"{name!r} matches several {kind} records", matches)
        place = matches[0]
        return _current(place) if current else place

    choices = {p.short_name: p for p in _table() if p.kind == kind}
    near = process.extract(name, list(choices), limit=3, score_cutoff=75)
    raise UnknownPlace(f"No {kind} named {name!r}", [n for n, _, _ in near])


_current = current  # lookup()'s `current` argument shadows the function


def county(name: str, **kwargs) -> Place:
    return lookup("county", name, **kwargs)


def place(name: str, **kwargs) -> Place:
    """An incorporated city or town. Census designated places: lookup("cdp", name)."""
    return lookup("place", name, **kwargs)


def tribal(name: str, **kwargs) -> Place:
    return lookup("tribal", name, **kwargs)


def counties(as_of: date | str | None = None, *, include_historical: bool = False) -> list[Place]:
    """Counties in force on a date (default today). include_historical: every county ever."""
    if include_historical:
        return all_places("county")
    on = date.fromisoformat(as_of) if isinstance(as_of, str) else (as_of or date.today())
    return [p for p in all_places("county") if p.in_force(on)]


def children(parent: str | Place, kind: str | None = None) -> list[Place]:
    """Places whose parent is this one: Lawrence County's cities, the state's counties."""
    geoid = parent.geoid if isinstance(parent, Place) else str(parent)
    return [p for p in _table() if p.parent == geoid and (kind is None or p.kind == kind)]


# --- Crosswalks ----------------------------------------------------------------------------

CROSSWALK_FIELDS = ("system_id", "geoid", "name_in_source", "evidence", "note")


@cache
def _crosswalk(system: str) -> tuple[dict, ...]:
    path = resources.files("sdplaces").joinpath(f"data/crosswalks/{system}.csv")
    if not path.is_file():
        known = sorted(
            f.name.removesuffix(".csv")
            for f in resources.files("sdplaces").joinpath("data/crosswalks").iterdir()
            if f.name.endswith(".csv")
        )
        raise ValueError(f"No crosswalk {system!r}. Known: {', '.join(known)}")
    with path.open(newline="") as f:
        return tuple(csv.DictReader(f))


def _same_id(a: str, b: str) -> bool:
    a, b = str(a).strip(), str(b).strip()
    return a == b or (a.isdigit() and b.isdigit() and int(a) == int(b))  # "07" == "7"


def from_id(system: str, system_id: str | int, *, as_of: date | str | None = None) -> Place:
    """The place an agency's own ID stands for: from_id("sos_election_county", 57) → Spink County.

    Every mapping comes from a row in data/crosswalks/<system>.csv, with its evidence. Rows may
    have valid_from / valid_to columns when a code meant different places over time; as_of picks
    the row in force. Unknown IDs raise UnknownPlace; nothing is inferred from order or position.
    """
    on = date.fromisoformat(as_of) if isinstance(as_of, str) else as_of
    rows = [r for r in _crosswalk(system) if _same_id(r["system_id"], system_id)]
    if on is not None:
        rows = [
            r
            for r in rows
            if (not r.get("valid_from") or date.fromisoformat(r["valid_from"]) <= on)
            and (not r.get("valid_to") or on < date.fromisoformat(r["valid_to"]))
        ]
    if not rows:
        raise UnknownPlace(f"No {system} ID {system_id!r}" + (f" in force on {on}" if on else ""))
    geoids = {r["geoid"] for r in rows}
    if len(geoids) > 1:
        raise AmbiguousPlace(
            f"{system} ID {system_id!r} maps to several places; pass as_of=",
            [get(g) for g in sorted(geoids)],
        )
    return get(geoids.pop())
