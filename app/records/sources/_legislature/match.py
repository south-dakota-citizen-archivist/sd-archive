"""Which legislator a session profile is: matching a SessionMemberId to the Historical
Listing's MemberId (the stable ID for a person), for profiles the hand-checked crosswalk
doesn't cover.

A profile can only be someone the Historical Listing says served in that chamber that
year (`Historical/Member/{id}`: each service's body, start and end year; no end year is
serving now). Among those, the names must agree:

- "name": the same family name, and given names that agree (the same, one a nickname of
  the other, or the listed given name is the person's middle name or nickname in quotes).
- "family + initial": the same family name and the same first initial, when that leaves one.
- "family within": one family name holds the other (Buhl / Buhl O'Donnell), given names
  agreeing.

Family names are compared without spaces (DeVries, De Vries). Some services list no body;
they count for either chamber.
- "a year off": the same tests against services one year from the session, for listings
  that end a service the year before or start it the year after.

Each test is tried in that order and taken only when exactly one person passes it; a tie
is narrowed by county. Nothing else is guessed: a profile no test settles is left
unmatched, with its candidates, for a person to decide. `evaluate()` runs the matcher over
the crosswalk's profiles to show how often it agrees with the hand-checked answer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import cache

import sdnames

from .build import api, crosswalk

BODIES = {"H": "H", "S": "S", "House": "H", "Senate": "S"}


@dataclass
class Person:
    member_id: int
    family: str
    given: set[str]  # normalized given names it could go by (first, middle, nickname)
    initials: set[str]
    services: list[tuple[str, int, int | None, str]] = field(default_factory=list)


def _norm(text) -> str:
    return sdnames.normalize(str(text or "")) or ""


def _forms(name: str) -> set[str]:
    out = set()
    for word in _norm(name).split():
        if len(word) > 1:
            out |= set(sdnames.given_forms(word))
    return out


@cache
def people() -> tuple[Person, ...]:
    from .crawl import raw_root

    out = []
    for path in sorted((raw_root() / "api" / "Historical" / "Member").glob("*.json")):
        m = api(f"Historical/Member/{path.stem}")
        if not m:
            continue
        names = f"{m.get('FirstName') or ''} {m.get('MiddleName') or ''}"
        nicknames = re.findall(r'"([^"]+)"|\'([^\']+)\'', names)
        given_text = " ".join([m.get("FirstName") or "", m.get("MiddleName") or "",
                               *(a or b for a, b in nicknames)])  # fmt: skip
        out.append(Person(
            member_id=m["MemberId"], family=_norm(m.get("LastName")).replace(" ", ""),
            given=_forms(given_text),
            initials={w[0] for w in _norm(given_text).split()},
            services=[(BODIES.get(s.get("Body"), s.get("Body")), s.get("StartYear") or 0,
                       s.get("EndYear"), _norm(s.get("County")))
                      for s in m.get("MemberServices") or []],
        ))  # fmt: skip
    return tuple(out)


def _serves(p: Person, body: str, year: int, slack: int = 0) -> bool:
    """Whether p served in body that year (a service with no body listed counts for both)."""
    return any(b in (body, None) and start - slack <= year <= (9999 if end is None else end) + slack
               for b, start, end, _ in p.services)  # fmt: skip


@dataclass
class Match:
    member_id: int | None
    how: str  # the test that settled it, or "unmatched"
    candidates: list[int] = field(default_factory=list)


def match(profile: dict, everyone: tuple[Person, ...] | None = None) -> Match:
    """profile: first_name, last_name, chamber (House/Senate or H/S), session_year, counties."""
    everyone = people() if everyone is None else everyone
    body = BODIES.get(profile.get("chamber"), profile.get("chamber"))
    year = profile.get("session_year") or 0
    family = _norm(profile.get("last_name")).replace(" ", "")  # DeVries is De Vries
    given = _forms(profile.get("first_name") or "")
    initial = _norm(profile.get("first_name"))[:1]
    counties = {_norm(c) for c in str(profile.get("counties") or "").split(",") if c.strip()}

    def one(found: list[Person], how: str) -> Match | None:
        if len(found) > 1 and counties:  # a tie: who served from one of its counties?
            narrowed = [p for p in found if any(c in counties for *_, c in p.services)]
            found = narrowed or found
        return Match(found[0].member_id, how) if len(found) == 1 else None

    tried: set[int] = set()
    for slack, suffix in ((0, ""), (1, ", a year off")):
        serving = [p for p in everyone if _serves(p, body, year, slack)]
        tried |= {p.member_id for p in serving if p.family == family}
        tests = [
            ("name", lambda p: p.family == family and bool(p.given & given)),
            ("family + initial", lambda p: p.family == family and initial in p.initials),
            ("family within", lambda p: bool(family) and p.family != family
             and (family in p.family or p.family in family)
             and bool(p.given & given)),
        ]  # fmt: skip
        for how, test in tests:
            found = [p for p in serving if test(p)]
            if found and (settled := one(found, how + suffix)):
                return settled
            if len(found) > 1:  # a tie no county settles: don't try looser tests
                return Match(None, "unmatched", sorted(p.member_id for p in found))
    return Match(None, "unmatched", sorted(tried))


def evaluate(profiles: list[dict]) -> dict:
    """The matcher on profiles the crosswalk has: agrees, disagrees (with examples) and left
    unmatched, by test."""
    xwalk = crosswalk()
    out = {"agree": {}, "disagree": {}, "unmatched": 0, "examples": []}
    for profile in profiles:
        truth = xwalk.get(profile["session_member_id"])
        if truth is None:
            continue
        got = match(profile)
        if got.member_id is None:
            out["unmatched"] += 1
        elif got.member_id == truth:
            out["agree"][got.how] = out["agree"].get(got.how, 0) + 1
        else:
            out["disagree"][got.how] = out["disagree"].get(got.how, 0) + 1
            if len(out["examples"]) < 20:
                out["examples"].append((profile["session_member_id"], profile.get("name"),
                                        profile.get("session_year"), got.member_id,
                                        truth))  # fmt: skip
    return out


def assign_legislators(profiles: list[dict]) -> None:
    """Set each profile's legislator_id and legislator_id_from: "crosswalk" (checked by hand),
    "matched (<test>)" (match()), or "member_id (<session profile>)": another profile with the
    same API MemberId (on the crosswalk, a MemberId is always one legislator, though one
    legislator can have two). None when nothing settles it."""
    xwalk = crosswalk()
    for p in profiles:
        if (found := xwalk.get(p["session_member_id"])) is not None:
            p["legislator_id"], p["legislator_id_from"] = found, "crosswalk"
            continue
        got = match(p)
        p["legislator_id"] = got.member_id
        p["legislator_id_from"] = f"matched ({got.how})" if got.member_id else None
    by_member: dict[int, set] = {}
    source: dict[int, int] = {}
    for p in profiles:
        if p["legislator_id"] is not None and p.get("member_id"):
            by_member.setdefault(p["member_id"], set()).add(p["legislator_id"])
            source.setdefault(p["member_id"], p["session_member_id"])
    for p in profiles:
        found = by_member.get(p.get("member_id"), set())
        if p["legislator_id"] is None and len(found) == 1:
            p["legislator_id"] = next(iter(found))
            p["legislator_id_from"] = f"member_id ({source[p['member_id']]})"
