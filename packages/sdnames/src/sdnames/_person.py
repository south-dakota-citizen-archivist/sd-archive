"""People's names: parts, and keys for finding the same person written differently."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

import jellyfish
from nameparser import Lexicon, Parser
from nicknames import NickNamer

from ._lexicon import GENERATIONS, family_length_back, family_length_front
from ._normalize import normalize

Order = Literal["auto", "given_first", "family_first"]

# Credentials nameparser doesn't know, seen after names in South Dakota filings (LPC-MH is
# the state's counselor license).
CREDENTIALS = ("lpc-mh", "lpc", "ncc", "lac", "cnp", "aprn", "macp")
_PARSER = Parser(lexicon=Lexicon.default().add(*CREDENTIALS, kind="credential"))
_NICKNAMES = NickNamer()
_SPACES = re.compile(r"\s+")
_TRAILING = re.compile(r"[\s,&/-]+$")
# Role words at the end of an owner string.
_ROLES = [
    (re.compile(r"\s+(?:CO[- ]?)?(?:TRUSTEES?|TTEES?|TRS)\.?$", re.I), "trustee"),
    (re.compile(r"\s+ET\.?\s?AL\.?$", re.I), "others"),
    (re.compile(r"\s+ET\.?\s?(?:UX|VIR)\.?$", re.I), "spouse"),
    (re.compile(r"\s+(?:JT\s?TEN|JTWROS|JT)\.?$", re.I), "joint"),
    (re.compile(r"\s+LIFE\s+ESTATE$", re.I), "life estate"),
]
_CARE_OF = re.compile(r"(?:^|\s+)(?:C/O|C\\O|%|ATTN:?)\s*", re.I)


def split_care_of(text: str) -> tuple[str, str]:
    """(principal, care of): "SMITH JOHN C/O ACME BANK" → ("SMITH JOHN", "ACME BANK");
    "% SHIRLEY K DUNLAP" → ("", "SHIRLEY K DUNLAP")."""
    parts = _CARE_OF.split(clean(text), maxsplit=1)
    return clean(parts[0]), clean(parts[1]) if len(parts) > 1 else ""


@dataclass(frozen=True)
class Person:
    given: str = ""
    middle: str = ""
    family: str = ""
    suffix: str = ""
    title: str = ""
    nickname: str = ""
    original: str = field(default="", compare=False)

    def __str__(self) -> str:
        return " ".join(
            p for p in (self.title, self.given, self.middle, self.family, self.suffix) if p
        )


def clean(text: str) -> str:
    """Spacing collapsed; a dangling '-', '&', ',' or '/' at the end (a cut-off field) dropped."""
    return _TRAILING.sub("", _SPACES.sub(" ", text or "").strip())


def strip_roles(text: str) -> tuple[str, list[str]]:
    """Role words at the end (TRUSTEE, ET AL, ET UX, JT TEN, LIFE ESTATE) removed and
    returned: (text, roles)."""
    roles = []
    changed = True
    while changed:
        changed = False
        for pattern, role in _ROLES:
            new = pattern.sub("", text)
            if new != text:
                text, changed = new, True
                roles.insert(0, role)
    return text, roles


def comma_order(text: str, order: Order = "auto") -> str:
    """The name rewritten as "FAMILY, GIVEN MIDDLE SUFFIX" where the family name is known (a
    compound surname, or the front of a family-first name), which nameparser reads right.
    Unchanged otherwise."""
    if "," in text:
        return text
    words = text.split()
    if len(words) < 2:
        return text
    if order == "family_first":
        n = family_length_front(words)
        rest = words[n:]
        suffix = [w for w in rest[:1] if normalize(w) in GENERATIONS]  # SMITH JR JOHN A
        rest = rest[len(suffix) :]
        return " ".join(words[:n]) + ", " + " ".join(rest + suffix)
    tail = []
    while len(words) > 2 and normalize(words[-1]) in GENERATIONS:
        tail.insert(0, words.pop())
    n = family_length_back(words)
    if not n:
        return text
    return " ".join(words[-n:]) + ", " + " ".join(words[:-n] + tail)


def person(text: str, order: Order = "auto") -> Person:
    """One person's name in parts. order="family_first" for sources that write LAST FIRST
    MIDDLE without a comma (many GIS layers); a comma always means family first."""
    original = text
    principal, care_of = split_care_of(text)
    text = clean(strip_roles(principal or care_of)[0])
    text = comma_order(text, order)
    p = _PARSER.parse(text)
    return Person(given=p.given, middle=p.middle, family=p.family, suffix=p.suffix,
                  title=p.title, nickname=p.nickname, original=original)  # fmt: skip


def given_forms(given: str) -> frozenset[str]:
    """A given name with the names it's a nickname of (BILL → WILLIAM, WILL, ...), normalized.
    Broad on purpose: for finding candidates, never for deciding a match."""
    g = normalize(given) or ""
    if not g:
        return frozenset()
    first = g.split()[0]
    return frozenset({first} | {normalize(c) for c in _NICKNAMES.canonicals_of(first.lower())})


def keys(p: Person) -> dict:
    """Blocking keys: records that share one are worth comparing."""
    family = normalize(p.family) or ""
    given = normalize(p.given) or ""
    squeezed = family.replace(" ", "")
    return {
        "family": family,
        "family_sound": jellyfish.metaphone(squeezed) if squeezed else "",
        "family_nysiis": jellyfish.nysiis(squeezed) if squeezed else "",
        "initial": given[:1],
        "given_forms": given_forms(p.given),
    }
