"""What kind of name a string is, and owner strings split into their parties."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from ._lexicon import family_length_front, given_names
from ._normalize import normalize
from ._person import Order, clean, person, split_care_of, strip_roles

Kind = Literal["person", "people", "organization", "government", "trust", "estate"]


def _words(*alternatives: str) -> re.Pattern:
    """Whole words in normalize()d text padded with a space at each end. An alternative
    ending in $ matches only at the end."""
    alts = [a[:-1] + r"(?= $)" if a.endswith("$") else a for a in alternatives]
    return re.compile(r" (?:" + "|".join(alts) + r") ")


# All patterns run on normalize()d text padded with a space at each end.
_GOVERNMENT = _words(
    r"(?:CITY|TOWN|COUNTY|STATE|VILLAGE|TOWNSHIP) OF", r"\w+ (?:COUNTY|TOWNSHIP)$",
    r"SCHOOL DIST(?:RICT)?", r"(?:SANITARY|WATER|FIRE|FIRE PROTECTION|CONSERVATION|ROAD|"
    r"IRRIGATION|WATER USER|RURAL WATER|AMBULANCE|HOSPITAL|PARK|IMPROVEMENT) DIST(?:RICT)?",
    r"UNITED STATES", r"USA", r"U S A", r"US GOVT", r"US GOVERNMENT",
    r"(?:US |U S |USDA )?FOREST SERVICE", r"BUREAU OF", r"DEPARTMENT OF", r"DEPT OF",
    r"BOARD OF (?:REGENTS|EDUCATION|COUNTY COMMISSIONERS|TRUSTEES)", r"GAME FISH",
    r"(?:\w+ )?SIOUX TRIBE", r"TRIBE$", r"TRIBAL", r"HOUSING AUTHORITY", r"COMMISSION",
    r"SCHOOL LANDS", r"STATE OF SOUTH DAKOTA", r"SD DEPT", r"S D DEPT", r"SIOUX TRI\w*$",
    r"(?:VOLUNTEER )?FIRE DEP\w*", r"VFD", r"PAVING DISTRICT", r"PUBLIC LIBRARY",
    r"BLDG AUTHORITY", r"BUILDING AUTHORITY", r"AIR FORCE", r"GOV T", r"INDEPENDENT SCHOOL",
    r"(?:COUNTY|CITY) HOUSING", r"SANITARY", r"SANITATION",
)  # fmt: skip
_LEGAL = _words(
    r"LLC", r"L L C", r"INC", r"INCORPORATED", r"CORP", r"CORPORATION", r"COMPANY", r"CO$",
    r"LTD", r"LIMITED", r"LLP", r"LLLP", r"LP", r"L P", r"PLLC", r"PC", r"P C", r"COOP",
    r"CO OP", r"COOPERATIVE", r"PARTNERSHIP", r"LIMITED PARTNERSHIP",
)  # fmt: skip
# Words only organizations use. Words that are also surnames (CHURCH, LODGE, BANKS) count
# only beside a word that marks them: FIRST LUTHERAN CHURCH, CHURCH OF, ELKS LODGE.
_ORG = _words(
    r"BANK", r"ASSOCIATION", r"ASSN", r"ASSOC", r"ASSOCIATES", r"CLUB", r"FARMS?", r"RANCH(?:ES)?",
    r"PROPERTIES", r"HOLDINGS", r"INVESTMENTS?", r"ENTERPRISES?", r"DEVELOPMENT", r"DEVELOPERS",
    r"FOUNDATION", r"HOSPITAL", r"CEMETERY", r"MINISTRY", r"MINISTRIES", r"FELLOWSHIP",
    r"FEDERATION", r"COUNCIL", r"SOCIETY", r"INSTITUTE", r"UNIVERSITY", r"COLLEGE", r"ACADEMY",
    r"CENTER", r"SERVICES?", r"GROUP", r"PARTNERS", r"VENTURES?", r"BROTHERS", r"BROS", r"SONS",
    r"HOMES", r"RESORT", r"MOTEL", r"HOTEL", r"CAMPGROUND", r"MANAGEMENT", r"CAPITAL",
    r"CATTLE", r"COMMUNITY", r"COMMITTEE", r"PAC", r"UNION", r"CREDIT", r"INSURANCE",
    r"ELECTRIC", r"TELEPHONE", r"ENERGY", r"POWER CO", r"OIL", r"SUPPLY", r"CONSTRUCTION",
    r"CONTRACTING", r"BUILDERS", r"REALTY", r"REAL ESTATE", r"APARTMENTS", r"STORAGE",
    r"MOTORS", r"AUTO", r"EQUIPMENT", r"IMPLEMENT", r"ELEVATOR", r"GRAIN", r"DAIRY", r"CLINIC",
    r"MEDICAL", r"HEALTH", r"PHARMACY", r"DIOCESE", r"PARISH", r"CONGREGATION", r"SYNOD",
    r"ALLIANCE", r"COALITION", r"NETWORK", r"FUND", r"LEAGUE", r"LEGION", r"VFW", r"WATER",
    r"(?:LUTHERAN|CATHOLIC|BAPTIST|METHODIST|PRESBYTERIAN|EVANGELICAL|REFORMED|CHRISTIAN|"
    r"BIBLE|COMMUNITY|FIRST|UNITED|MENNONITE|HUTTERIAN|EPISCOPAL|CONGREGATIONAL) CHURCH",
    r"CHURCH OF", r"(?:ELKS|MASONIC|MOOSE|EAGLES) LODGE", r"LODGE NO", r"POST NO",
    r"ELECTORS", r"HOMEOWNERS?", r"HOA", r"ASSO", r"ASCN", r"ASSCN", r"ASC", r"CHAMBER",
    r"HABITAT", r"HUMANITY", r"MUSEUM", r"MALL", r"RENTALS?", r"SOLUTIONS", r"CONSULTING",
    r"FUNERAL", r"RETIREMENT", r"ACQUISITIONS?", r"ENTERTAINMENT", r"GRAZING", r"CITIZENS",
    r"JAYCEES", r"SANCTUARY", r"AUTOMOTIVE", r"MINING", r"HISTORICAL", r"SOC", r"GOLF",
    r"CATHEDRAL?", r"CATHEDR", r"GOSPEL", r"BRETHE?REN", r"ASSEMBLY OF GOD", r"CONVENTION",
    r"MOTORCYCLE", r"ESTATES", r"LC", r"LPLP", r"MENN", r"COMM", r"ADVOCATES?", r"AGENCY",
)  # fmt: skip
# Words that are also surnames: an organization only where they aren't a family name
# (JEFFREY D CHURCH, CHURCH, JEFFREY D).
_SURNAME_ORG_WORDS = frozenset(["CHURCH", "LODGE", "CHAPEL", "TEMPLE", "MISSION", "COLONY"])
_TRUST = _words(
    r"TRUSTS?", r"TRUS$", r"TRU$", r"TRST", r"REVOCABLE", r"IRREVOCABLE", r"LIVIN$", r"LIVING$",
    r"(?:TRADITIONAL|ROTH|S D|SD|ROLLOVER|SEP) IRA", r"IRA (?:ACCT|ACCOUNT|CUSTODIAN)",
    r"(?:LIVING|REVOCABLE|REV|FAMILY|IRREVOCABLE|IRREV|LIV) TR",
)  # fmt: skip
_ESTATE = re.compile(r"(?<!REAL)(?<!LIFE) (?:ESTATE|EST) OF |(?<!REAL)(?<!LIFE) ESTATE $| HEIRS ")

_AND = re.compile(r"\s*&\s*|\s+(?:AND|OR)\s+", re.I)
# In a given-first list, a comma separates names ("ANN LEE, BOB RAY"), except before a suffix
_LIST_COMMA = re.compile(r",(?!\s*(?:JR|SR|II|III|IV|V|ESQ|MD|PHD|DDS|CPA)\b)\s*", re.I)
_INITIAL = re.compile(r"^[A-Z]\.?$", re.I)


@dataclass(frozen=True)
class Party:
    """One party named in an owner or filer string. name is None for an unnamed party
    (ET AL: others; ET UX: a spouse)."""

    name: str | None
    role: str | None = None


def _surname_org_word(text: str) -> bool:
    """CHURCH (or LODGE, CHAPEL...) used as an organization word: not the first word (where a
    family-first name keeps it) and not the last after a given name or an initial."""
    words = normalize(text).split()
    for i, w in enumerate(words):
        if w not in _SURNAME_ORG_WORDS or i == 0:
            continue
        before = words[i - 1]
        if i == len(words) - 1 and (len(before) == 1 or before in given_names()):
            continue
        return True
    return False


def _single_kind(text: str) -> Kind | None:
    n = f" {normalize(text)} "
    if _GOVERNMENT.search(n):
        return "government"
    if _LEGAL.search(n) or _ORG.search(n) or _surname_org_word(text):
        return "organization"
    if _TRUST.search(n):
        return "trust"
    if _ESTATE.search(n):
        return "estate"
    return None


def kind(text: str) -> Kind:
    """person, people (more than one person), organization, government, trust or estate.
    A care-of party (C/O, %) doesn't count."""
    principal, care_of = split_care_of(text)
    found = _single_kind(principal or care_of)
    if found:
        return found
    named = [p for p in parties(text) if p.role != "care of"]
    return "people" if len(named) > 1 else "person"


def _given_only(words: list[str]) -> bool:
    """Looks like given names with no family name: JANE, JANE B, MARY ANN."""
    return 1 <= len(words) <= 2 or (len(words) == 3 and bool(_INITIAL.match(words[-1])))


def parties(text: str, order: Order = "auto") -> list[Party]:
    """The parties in an owner or filer string, each name written so person() reads it.

    KOLBE, JANE B & ROBERT E → KOLBE, JANE B | KOLBE, ROBERT E
    SMITH JOHN A & MARY L (order="family_first") → SMITH, JOHN A | SMITH, MARY L
    JOHN AND MARY SMITH → SMITH, JOHN | MARY SMITH
    LEWIS, JAMES CARL ETUX → LEWIS, JAMES CARL | (spouse)
    ANN LEE, BOB RAY, JR. AND CY DOE (order="given_first") → ANN LEE | BOB RAY, JR. | CY DOE
    Organizations, governments, trusts and estates are one party."""
    text = clean(text)
    text, care_of = split_care_of(text)
    if not text:
        return [Party(care_of, "care of")] if care_of else []
    out: list[Party] = []
    text, roles = strip_roles(text)
    text = clean(text)
    if _single_kind(text):
        out.append(Party(text, None))
    else:
        out.extend(_people(text, order, "trustee" if "trustee" in roles else None))
    for role in roles:
        if role in ("others", "spouse"):
            out.append(Party(None, role))
        elif role != "trustee":
            out[:] = [Party(p.name, p.role or role) if p.name else p for p in out]
    if care_of:
        out.append(Party(care_of, "care of"))
    return out


def _people(text: str, order: Order, role: str | None) -> list[Party]:
    pieces = [clean(p.strip(" ,")) for p in _AND.split(text)]
    if order == "given_first":
        pieces = [clean(q) for p in pieces for q in _LIST_COMMA.split(p)]
    pieces = [p for p in pieces if p]
    if not pieces:
        return []
    names: list[str] = []
    first = pieces[0]
    if order == "family_first" or (order == "auto" and "," in first):
        if "," in first:
            family = first.split(",", 1)[0].strip()
        else:
            words = first.split()
            family = " ".join(words[: family_length_front(words)])
            first = f"{family}, {' '.join(words[len(family.split()) :])}".rstrip(", ")
        names.append(first)
        for piece in pieces[1:]:
            if "," in piece:
                names.append(piece)
            elif _given_only(piece.split()):
                names.append(f"{family}, {piece}")
            elif order == "family_first":
                names.append(_family_first_comma(piece))
            else:
                names.append(piece)
    else:
        # given first: JOHN AND MARY SMITH, JOHN A & MARY L SMITH share the last family name;
        # JOHN SMITH AND MARY JONES don't.
        names = list(pieces)
        if len(pieces) > 1 and len(pieces[-1].split()) >= 2:
            family = person(pieces[-1]).family
            names = [f"{family}, {p}" if i < len(pieces) - 1 and _bare_given(p.split()) else p
                     for i, p in enumerate(pieces)]  # fmt: skip
    return [Party(n, role) for n in names]


def _bare_given(words: list[str]) -> bool:
    """One given name, perhaps with an initial: JOHN, JOHN A."""
    return len(words) == 1 or (len(words) == 2 and bool(_INITIAL.match(words[1])))


def _family_first_comma(text: str) -> str:
    words = text.split()
    n = family_length_front(words)
    return " ".join(words[:n]) + ", " + " ".join(words[n:])


_LEGAL_STRIP = re.compile(
    r" (?:LLC|L L C|INC|INCORPORATED|CORP|CORPORATION|COMPANY|CO|LTD|LIMITED|LLP|LLLP|LP|L P|"
    r"PLLC|PC|P C)(?= |$)"
)


def org_key(text: str) -> str:
    """An organization's name for matching: normalized, legal forms (LLC, INC, CORP...) and a
    leading THE removed. BLACK HILLS CORP and Black Hills Corporation share one key."""
    n = f" {normalize(text)}"
    n = _LEGAL_STRIP.sub("", n).strip()
    return n.removeprefix("THE ").strip()


_FTM = {
    "person": "Person",
    "people": "Person",
    "organization": "Organization",
    "government": "PublicBody",
    "trust": "LegalEntity",
    "estate": "LegalEntity",
}


def ftm_schema(text: str) -> str:
    """The Follow the Money schema for an entity with this name: Person, Company (with a
    legal form), Organization, PublicBody or LegalEntity."""
    k = kind(text)
    if k == "organization" and _LEGAL.search(f" {normalize(text)} "):
        return "Company"
    return _FTM[k]
