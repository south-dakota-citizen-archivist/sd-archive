"""The phone book's JSON (a ServiceNow portal) → entries. Pure: no fetching, no database."""

from __future__ import annotations

import sdnames
from records.sources._tables import LayoutChanged

__all__ = ["LayoutChanged", "agencies", "entries", "person_parts"]


def agencies(page_json: dict) -> list[tuple[str, str]]:
    """(ID, name) of each agency in the page's "Filter by Agency" facet."""

    def find(o):
        if isinstance(o, dict):
            if o.get("label") == "Filter by Agency" and isinstance(o.get("choices"), list):
                return o["choices"]
            for v in o.values():
                if (found := find(v)) is not None:
                    return found
        elif isinstance(o, list):
            for v in o:
                if (found := find(v)) is not None:
                    return found
        return None

    choices = find(page_json)
    if not choices:
        raise LayoutChanged("no 'Filter by Agency' facet in the page data")
    return [(c["value"], " ".join(c["label"].split())) for c in choices]


# State agency abbreviations. An entry that starts with one is an office or unit, not a person
# ("DOC-SDSP COMS UNIT A", "DSS HSC Laboratory", "DOE ArchivistEmail").
AGENCY_CODES = frozenset({
    "ATG", "BFM", "BHR", "BIT", "BOR", "DANR", "DCR", "DENR", "DHS", "DLR", "DOA", "DOC", "DOE",
    "DOH", "DOM", "DOR", "DOT", "DPS", "DSS", "DTR", "DVA", "GFP", "GOV", "LRC", "SDHDA", "SDRS",
    "SIB", "SOS", "UJS",
})  # fmt: skip


def is_office(name: str) -> bool:
    first = name.split()[0].split("-")[0] if name.split() else ""
    return first in AGENCY_CODES or sdnames.kind(name) != "person"


def person_parts(name: str) -> tuple[str | None, str | None]:
    """Given and family name for a person's entry; (None, None) for an office or unit."""
    if is_office(name):
        return None, None
    p = sdnames.person(name, "given_first")
    return p.given or None, p.family or None


def entries(result: dict) -> list[dict]:
    """The entries on one page of results: name, phone (displayed and E.164), unit ID."""
    data = result.get("result", {}).get("data")
    if not isinstance(data, dict) or "all_items" not in data:
        raise LayoutChanged("no result.data.all_items in the response")
    out = []
    for item in data["all_items"]:
        name_cell = item[0] if item and isinstance(item[0], dict) else {}
        name = " ".join(str(name_cell.get("field_value") or "").split())
        if not name:
            raise LayoutChanged(f"an entry without a name: {item!r:.200}")
        phone = item[1] if len(item) > 1 and isinstance(item[1], dict) else {}
        out.append({
            "full_name": name,
            "phone": " ".join(str(phone.get("field_value") or "").split()) or None,
            "phone_e164": (phone.get("field") or {}).get("value") or None,
            "division_id": item[2] if len(item) > 2 and isinstance(item[2], str) else None,
        })  # fmt: skip
    return out
