"""Street address standardization: the one place addresses are parsed and turned into keys
for matching records across sources (permits, filings, agendas, county parcels).

Moved from whats-up-in-spearfish (scrapers/address.py) and made statewide. Needs the
`address` extra (usaddress, usaddress-scourgify).

Parsing is usaddress (a probabilistic US address parser); abbreviations follow USPS
Publication 28 via usaddress-scourgify's tables. A thin layer around them handles what
local records throw at it:

- several addresses in one field: "2545 & 2555 CLEAR SPRING RD"
- tags glued to the street: "540 32ND ST-GA OFFICE", "20189 US HWY 85-CAMPGROUND"
- SAINT/ST in street names: "6845 SAINT ONGE RD" = "6845 ST ONGE RD"
- a city and state at the end, in any US state ("…, Spearfish, SD 57783", "…, Minneapolis, MN"),
  or an SD place name with no comma ("100 MAIN ST HILL CITY"). The city and state are kept on
  the Address, outside the keys. A town is only stripped at the very end, so "123 CUSTER ST"
  and "100 LEAD ST" keep their street names.
- text that isn't an address at all ("Intersection of …"), which is rejected

Two keys per address:

- key():   number + direction + street + type, with the direction moved to the front, so
           "515 27TH ST N" and "515 N 27TH ST" match ("515 N 27TH ST")
- loose(): the same without any direction. Sources disagree on directions ("1440 N MAIN ST"
           vs "1440 MAIN ST"), so callers fall back to it only when it identifies one place.

Units ("UNIT 3", "STE B", "#4") are kept separately and left out of both keys: a unit belongs
to its building's site.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache, lru_cache

import usaddress
from scourgify.address_constants import DIRECTIONAL_REPLACEMENTS, STREET_TYPE_ABBREVIATIONS

from ._places import all_places

DIRECTIONS = {"N", "S", "E", "W", "NE", "NW", "SE", "SW"}
_DIRS = {**DIRECTIONAL_REPLACEMENTS, **{d: d for d in DIRECTIONS}}
_TYPES = {k.upper(): v.upper() for k, v in STREET_TYPE_ABBREVIATIONS.items()}
_GLUED_TAG = re.compile(r"(?<=[A-Z0-9])-(?=[A-Z]{2,})")  # "ST-GA OFFICE" → "ST GA OFFICE"
_UNIT_WORDS = {"UNIT", "STE", "SUITE", "APT", "BLDG", "LOT", "SPACE", "TRLR", "RM", "#"}

STATES = [
    "AL",
    "AK",
    "AZ",
    "AR",
    "CA",
    "CO",
    "CT",
    "DE",
    "DC",
    "FL",
    "GA",
    "HI",
    "ID",
    "IL",
    "IN",
    "IA",
    "KS",
    "KY",
    "LA",
    "ME",
    "MD",
    "MA",
    "MI",
    "MN",
    "MS",
    "MO",
    "MT",
    "NE",
    "NV",
    "NH",
    "NJ",
    "NM",
    "NY",
    "NC",
    "ND",
    "OH",
    "OK",
    "OR",
    "PA",
    "RI",
    "SC",
    "SD",
    "TN",
    "TX",
    "UT",
    "VT",
    "VA",
    "WA",
    "WV",
    "WI",
    "WY",
]
# ", SD 57783", " S.D.", ", South Dakota", ", MN 55401-1234" at the very end
_ZIP = r"(?P<zip>\d{5}(?:-\d{4})?)"
# ", SD 57783", " SD", " S D", ", South Dakota" at the very end
_SD_TAIL = re.compile(r",?\s*\b(?P<state>SD|S\s?D|SOUTH DAKOTA)\b\s*" + _ZIP + r"?\s*$")
# Another state only with a ZIP after it ("WA 98122") or a comma before it (", MN"): many state
# codes are also street words (CT = court, MT = mount, LA, NE…), so "1103 QUAIL CT" stays a street
_OTHER_STATE_TAIL = re.compile(
    r"(?:,\s*(?P<state1>" + "|".join(STATES) + r")\b\s*" + _ZIP.replace("zip", "zip1") + r"?"
    r"|\s(?P<state2>" + "|".join(STATES) + r")\s+" + _ZIP.replace("zip", "zip2") + r")\s*$"
)
_ZIP_TAIL = re.compile(r",?\s*(?P<zip>\d{5}(?:-\d{4})?)\s*$")
# Misspellings seen in records, by the place they mean
_KNOWN_MISSPELLINGS = {"SPEARISH": "SPEARFISH"}


@dataclass(frozen=True)
class Address:
    number: str
    street: str  # street name, e.g. "MAIN", "27TH", "ST ONGE", "US HWY 85"
    type: str = ""  # USPS street type, e.g. "ST", "DR"
    direction: str = ""  # pre- or post-directional, e.g. "N"
    unit: str = ""
    city: str = ""  # as written, uppercased ("SPEARFISH"); not part of the keys
    state: str = ""  # "SD", "MN"…; not part of the keys
    zip: str = ""

    def key(self) -> str:
        return " ".join(x for x in (self.number, self.direction, self.street, self.type) if x)

    def loose(self) -> str:
        return " ".join(x for x in (self.number, self.street, self.type) if x)


# Street type words, full and abbreviated ("LANE", "LN", "MOUNTAIN", "MTN")
_STREET_WORDS = set(_TYPES) | set(_TYPES.values())


@cache
def _sd_place_names() -> frozenset[str]:
    names = {p.short_name.upper() for p in all_places("place") + all_places("cdp")}
    names |= set(_KNOWN_MISSPELLINGS)
    return frozenset(re.sub(r"\bSAINT\b", "ST", n).replace(".", "") for n in names)


def _is_sd_place(name: str) -> bool:
    return " ".join(name.replace(".", " ").split()) in _sd_place_names()


@cache
def _sd_place_tail() -> re.Pattern:
    """An SD city, town or CDP name at the very end of an address, longest names first."""
    alternatives = "|".join(re.escape(n) for n in sorted(_sd_place_names(), key=len, reverse=True))
    return re.compile(rf"\s+(?P<city>{alternatives})$")


def _clean(text: str) -> tuple[str, str, str, str]:
    """(street part, city, state, zip) from free text."""
    s = " ".join((text or "").upper().replace("\n", " ").split())
    s = s.split(";")[
        0
    ]  # "2515 SAPPHIRE LANE, SPEARFISH; LOCATED SOUTH OF …": a description follows
    s = " ".join(s.replace(".", " ").split())
    state = zip_code = city = ""
    if m := _SD_TAIL.search(s):
        state, zip_code = "SD", m["zip"] or ""
        s = s[: m.start()].rstrip(" ,")
    elif m := _OTHER_STATE_TAIL.search(s):
        # Out of state: keep the text whole for usaddress, which tags its city (see _parse_one)
        state = m["state1"] or m["state2"]
        zip_code = m["zip1"] or m["zip2"] or ""
        return " ".join(re.sub(r"[^\w\s#&/-]", " ", s).split()), "", state, zip_code
    elif m := _ZIP_TAIL.search(s):
        zip_code = m["zip"]
        s = s[: m.start()].rstrip(" ,")
    # ", CITY" after the street: only a known SD place, or any name when a state followed it
    # ("…, Minneapolis, MN"). Not ", UNIT 3", and not the tail of a legal description (", BHM").
    if "," in s:
        head, tail = s.rsplit(",", 1)
        tail = tail.strip()
        if (
            tail
            and not re.search(r"\d", tail)
            and tail.split()[0] not in _UNIT_WORDS
            and tail not in DIRECTIONS  # "388 CENTAURI AVENUE, S, SD": S is South, not a city
            and (state or _is_sd_place(tail))
        ):
            city, s = tail, head.strip()
    s = _GLUED_TAG.sub(" ", s)
    s = re.sub(r"[^\w\s#&/-]", " ", s)  # commas and other punctuation
    s = re.sub(r"\bSAINT\b", "ST", s)
    s = " ".join(s.split())
    # An SD town with no comma: only at the very end, only after what is already a complete
    # street ("100 MAIN ST HILL CITY"), and never a town named like a street type: Lane is a
    # town, so "1 ROSE LANE" keeps its LANE.
    if not city and (m := _sd_place_tail().search(s)):
        rest = s[: m.start()].split()
        if (
            m["city"] not in _STREET_WORDS
            and len(rest) >= 2
            and re.match(r"^\d", rest[0])
            and (rest[-1] in _STREET_WORDS or rest[-1].isdigit())  # "104 W HUDSON" isn't one
        ):
            city, s = m["city"], " ".join(rest)
    city = _KNOWN_MISSPELLINGS.get(city, city)
    return s, city, state, zip_code


def _split_multiple(s: str) -> list[str]:
    """'2545 & 2555 CLEAR SPRING RD' → ['2545 CLEAR SPRING RD', '2555 CLEAR SPRING RD']."""
    m = re.match(r"^(\d+[A-Z]?)\s*(?:&|AND|/)\s*(\d+[A-Z]?)\s+(.+)$", s)
    if m:
        return [f"{m.group(1)} {m.group(3)}", f"{m.group(2)} {m.group(3)}"]
    return [s]


def _abbrev_type(word: str) -> str:
    return _TYPES.get(word, word)


def _parse_one(s: str, city: str, state: str, zip_code: str) -> Address | None:
    try:
        tags, _ = usaddress.tag(s)
    except usaddress.RepeatedLabelError:
        return None
    number = tags.get("AddressNumber", "")
    if not re.fullmatch(r"\d+[A-Z]?(-\d+)?", number or ""):
        return None
    pre_type = tags.get("StreetNamePreType", "")
    name = tags.get("StreetName", "")
    # usaddress reads "US HWY 85-CAMPGROUND" as a pre-type plus a glued name; keep the route number
    if pre_type:
        name = f"{_abbrev_type(pre_type)} {re.match(r'[A-Z0-9]+', name).group(0) if name else ''}"
        name = name.strip()
    if not name:
        return None
    post_type = _abbrev_type(tags.get("StreetNamePostType", ""))
    # "1300 N Avenue" is North Avenue: a name that's only a direction is spelled out
    if name in ("N", "S", "E", "W") and post_type:
        name = {"N": "NORTH", "S": "SOUTH", "E": "EAST", "W": "WEST"}[name]
    pre_dir = _DIRS.get(tags.get("StreetNamePreDirectional", ""), "")
    post_dir = _DIRS.get(tags.get("StreetNamePostDirectional", ""), "")
    if state and state != "SD" and not city:
        city = tags.get("PlaceName", "")  # an out-of-state address, left whole for usaddress
    # "540 32ND ST GA OFFICE": usaddress files "ST GA OFFICE" as a place name; recover the type
    if not post_type and (place := tags.get("PlaceName", "")) and place != city:
        first = place.split()[0]
        if first in _TYPES:
            post_type = _abbrev_type(first)
    # A name ending in a street type word ("CLEAR SPRING RD" mis-tagged) gets split back out
    words = name.split()
    if not post_type and not pre_type and len(words) > 1 and words[-1] in _TYPES:
        name, post_type = " ".join(words[:-1]), _abbrev_type(words[-1])
    unit = " ".join(
        x for x in (tags.get("OccupancyType", ""), tags.get("OccupancyIdentifier", "")) if x
    )
    if not unit and post_type and (extra := tags.get("OccupancyIdentifier", "")):
        unit = extra
    return Address(
        number=number,
        street=name,
        type=post_type,
        direction=pre_dir or post_dir,
        unit=unit,
        city=city,
        state=state,
        zip=zip_code,
    )


@lru_cache(maxsize=50_000)
def parse(text: str) -> tuple[Address, ...]:
    """Every street address in a field (usually one); () if there isn't one."""
    s, city, state, zip_code = _clean(text)
    if not s or not re.match(r"^\d", s):
        return ()
    out = []
    for part in _split_multiple(s):
        if a := _parse_one(part, city, state, zip_code):
            out.append(a)
    return tuple(out)


def key(text: str) -> str:
    """Matching key for the first address in text ('' if none)."""
    parsed = parse(text)
    return parsed[0].key() if parsed else ""


def loose(text: str) -> str:
    parsed = parse(text)
    return parsed[0].loose() if parsed else ""


def keys(text: str) -> list[str]:
    """Keys for every address in text (a permit can cover '3813 & 3815 Rock Ridge')."""
    return [a.key() for a in parse(text)]
