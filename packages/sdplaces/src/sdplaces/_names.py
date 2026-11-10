"""Normalizing place names for matching. Matching is exact on these keys, never fuzzy."""

from __future__ import annotations

import re
import unicodedata

# Words that only say what kind of place it is, stripped before matching
_SUFFIX = {
    "county": re.compile(r"\s+(COUNTY|CNTY|CO)$"),
    "place": re.compile(r"\s+(CITY|TOWN|VILLAGE)$"),
    "cdp": re.compile(r"\s+CDP$"),
}
# Words removed anywhere in the name, by kind
_REMOVE = {
    "school_district": re.compile(r"\b(SCHOOL DISTRICT|SCHOOL DIST|SCHOOLS?|DISTRICT|SD)\b"),
    "sldu": re.compile(r"\b(SOUTH DAKOTA|STATE|SENATE|LEGISLATIVE|DISTRICT|DIST|NO)\b"),
    "sldl": re.compile(r"\b(SOUTH DAKOTA|STATE|HOUSE|LEGISLATIVE|DISTRICT|DIST|NO)\b"),
}
_PREFIX = {"place": re.compile(r"^(CITY|TOWN) OF\s+"), "county": re.compile(r"^COUNTY OF\s+")}
_STATE_TAIL = re.compile(r",?\s+(SD|S\s*D|SOUTH DAKOTA)$")


def _base(name: str, kind: str) -> str:
    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    s = " ".join(s.upper().replace(".", " ").split())
    s = _STATE_TAIL.sub("", s)
    if kind in _PREFIX:
        s = _PREFIX[kind].sub("", s)
    if kind in _SUFFIX:
        s = _SUFFIX[kind].sub("", s)
    if kind in _REMOVE:
        s = " ".join(_REMOVE[kind].sub(" ", s).split())
    if kind in ("sldu", "sldl"):
        s = re.sub(r"^0+(?=\d)", "", s)  # "026A" = "26A"
    s = re.sub(r"\bSAINT\b", "ST", s)
    s = re.sub(r"\bFT\b", "FORT", s)
    return s.replace("&", " AND ")


def spaced_key(name: str, kind: str) -> str:
    """'Charles Mix County' → 'CHARLES MIX'; 'White Horse' and 'Whitehorse' stay different."""
    return " ".join(re.sub(r"[^A-Z0-9 ]", " ", _base(name, kind)).split())


def compact_key(name: str, kind: str) -> str:
    """Spaces ignored too: 'Mc Cook' = 'McCook', 'Bon Homme' = 'BonHomme'."""
    return re.sub(r"[^A-Z0-9]", "", _base(name, kind))
