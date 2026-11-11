"""The board list and each board's member page → rows. Pure: no fetching, no database."""

from __future__ import annotations

import re
from urllib.parse import urljoin

import sdplaces
from records.sources._tables import LayoutChanged, attr, document, text
from sddocs.clean import parse_date

from .schema import Member

__all__ = ["LayoutChanged", "board_ids", "city_place", "members"]

# span id prefixes in the member table → fields
SPANS = {
    "name": "gvBoardMembers_lblMemberFirstName",
    "position": "gvBoardMembers_lblMemberposition",
    "city": "gvBoardMembers_lblMemberCity",
    "term_end_raw": "gvBoardMembers_lblMemberTermEnd",
    "party": "lblParty",
    "bio": "gvBoardMembers_lblAdditionalInfo",
}


def board_ids(html: str | bytes) -> list[int]:
    """Every board on the A–Z list, in list order."""
    tables = document(html).cssselect("table#gvResults")
    if not tables:
        raise LayoutChanged("no #gvResults table on the board list")
    ids = []
    for a in tables[0].iter("a"):
        if (m := re.search(r"BoardID=(\d+)", a.get("href") or "")) and int(m[1]) not in ids:
            ids.append(int(m[1]))
    if not ids:
        raise LayoutChanged("the board list has no boards")
    return ids


def _drop_label(value: str, label: str) -> str:
    """'Biography: …' → '…' (the label as a prefix, not a set of characters)."""
    return value[len(label) :].strip() if value.startswith(label) else value


def city_place(city: str | None) -> str | None:
    """The fully qualified GEOID of the one South Dakota city, town or census designated place
    with this name; None if there's none, or several (nothing is guessed)."""
    if not city:
        return None
    for kind in ("place", "cdp"):
        try:
            return sdplaces.lookup(kind, city).geoidfq
        except sdplaces.UnknownPlace:
            continue
    return None


def members(html: str | bytes, board_id: int, base_url: str) -> list[Member]:
    """The members on one board's page. A board with no members yet has an empty table."""
    root = document(html)
    title = root.cssselect("div#boardTitle")
    if not title:
        raise LayoutChanged(f"board {board_id}: no #boardTitle")
    board = title[0].cssselect("a#boardmenu_lnkBoard")
    call, email = (title[0].cssselect(f"a#boardmenu_contact{k}") for k in ("Call", "Email"))
    agency = root.cssselect("div#agencyAffiliation")
    agency_link = agency[0].cssselect("a[href]") if agency else []
    shared = {
        "board_id": board_id,
        "board_name": text(board[0]) if board else "",
        "board_website": attr(board[0], "href") if board else None,
        "board_phone": attr(call[0], "title") if call else None,
        "board_email": attr(email[0], "title") if email else None,
        "agency_affiliation": _drop_label(text(agency[0]), "Agency Affiliation:") or None
        if agency else None,
        "agency_website": attr(agency_link[0], "href") if agency_link else None,
    }  # fmt: skip
    if not shared["board_name"]:
        raise LayoutChanged(f"board {board_id}: no board name")
    out = []
    for tr in root.cssselect("table#gvBoardMembers tr"):
        cells = tr.findall("td")
        if len(cells) < 2:
            continue
        values = {}
        for field, prefix in SPANS.items():
            span = cells[1].xpath(f".//span[starts-with(@id, '{prefix}')]")
            values[field] = text(span[0]) if span else ""
        if not values["name"]:
            raise LayoutChanged(f"board {board_id}: a member row without a name")
        bio = _drop_label(values["bio"], "Biography:")
        img = cells[0].find(".//img")
        out.append(Member(
            **shared, name=values["name"], position=values["position"] or None,
            city=values["city"] or None, city_place=city_place(values["city"]),
            term_end=parse_date(values["term_end_raw"]) if values["term_end_raw"] else None,
            term_end_raw=values["term_end_raw"], party=values["party"] or None,
            bio=bio if bio and bio != "Not Specified" else None,
            picture_url=urljoin(base_url, img.get("src")) if img is not None and img.get("src")
            else None,
        ))  # fmt: skip
    return out
