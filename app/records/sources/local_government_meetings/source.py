"""Meetings of South Dakota's cities, counties and school boards, from the state's Local
Government Boards and Commissions portal (sdlocalgov.appssd.sd.gov): one record per meeting,
with its board, the body meeting, when and where, and the files and links posted for it.

The boards come from the portal's list of all boards, each one's kind from the search by
kind. A board's page shows its upcoming meetings and one archive year; each other year the
archive offers is read too. The meeting ID is the key. Agendas, packets, minutes and other
documents are the record's documents; audio recordings are listed in the record but not
downloaded. Each record is tied to the board's city, county or school district.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import date
from functools import cache

import sdplaces
from records.sources import DocumentLink, Found, Item, Source
from records.sources._places import city_place
from records.sources._sdcfr import form_fields

from . import parse

STATE = "0400000US46"
NOT_DOWNLOADED = {"Audio"}


@cache
def _school_districts() -> dict[str, str]:
    """School district name (without "School District" and its number, hyphens as spaces) →
    GEOID, where only one district has that name."""
    names: dict[str, list[str]] = {}
    for d in sdplaces.all_places("school_district"):
        name = re.sub(r"\s+\d+-\d+$", "", d.short_name).upper().replace("-", " ")
        names.setdefault(name, []).append(d.geoidfq)
    return {n: g[0] for n, g in names.items() if len(g) == 1}


_COUNTY = re.compile(r"^(?:Board of )?(.+?) County\b")
_CITY_EXTRAS = re.compile(
    r"^(?:City|Town) of |,? (?:City|Town) of$|"
    r" (?:City Council|City Commission|Board of City Commissioners|"
    r"Board of Trustees|Town Board)$"
)
# names the portal spells differently from the census
SPELLINGS = {"DESMET": "DE SMET"}


def place_name(name: str, kind: str | None) -> str:
    """The city, county or district a board's name names ("Lawrence County Commission" →
    "Lawrence", "Spearfish, City of" → "Spearfish", "Miller School District" → "Miller")."""
    if kind == "county" and (m := _COUNTY.match(name)):
        return m.group(1)
    if kind == "school board" or name.endswith("School District"):
        return re.sub(r"\s+School District$", "", name)
    return _CITY_EXTRAS.sub("", name).strip()


def board_place(name: str, kind: str | None) -> str | None:
    """The board's jurisdiction: its city, county or school district (None unless exactly
    one matches the name)."""
    base = place_name(name, kind)
    if kind == "school board" or name.endswith("School District"):
        districts = _school_districts()
        key = base.upper().replace("-", " ")  # "Viborg-Hurley" / "Viborg Hurley"
        key = SPELLINGS.get(key, key)
        # the census and the portal differ on "Area" ("Chester Area" / "Miller Area")
        for name in (key, re.sub(r" AREA$", "", key), f"{key} AREA"):
            if name in districts:
                return districts[name]
        return None
    if kind == "county":
        try:
            return sdplaces.lookup("county", base).geoidfq
        except sdplaces.UnknownPlace:
            return None
    return city_place(base)


def board_kinds(fetcher, home: bytes) -> dict[str, str]:
    """Board ID → city, county or school board, from the search by kind."""
    out = {}
    for value, kind in parse.TYPES.items():
        data = {**form_fields(home), "__EVENTTARGET": "header$ddlAgency",
                "__EVENTARGUMENT": "", "header$ddlAgency": value}  # fmt: skip
        page = fetcher.request("POST", parse.SITE, data=data, timeout=120).content
        for board_id, _ in parse.boards(page):
            out[board_id] = kind
    return out


def board_pages(fetcher, board_id: str, today: date) -> list[bytes]:
    """A board's page, and its page for each other archive year."""
    url = parse.board_url(board_id)
    store = getattr(fetcher, "store", None)
    first = fetcher.request("GET", url, timeout=120).content
    pages = [first]
    if store is not None:
        store.save(f"boards/{today:%Y-%m-%d}/{board_id}.html", first, url)
    shown, *others = parse.years(first) or [None]
    for year in others:
        data = {**form_fields(first), "__EVENTTARGET": "ddlArchiveYear", "__EVENTARGUMENT": "",
                "ddlArchiveYear": year}  # fmt: skip
        page = fetcher.request("POST", url, data=data, timeout=120).content
        if store is not None:
            store.save(f"boards/{today:%Y-%m-%d}/{board_id}-{year}.html", page, url,
                       method="POST")  # fmt: skip
        pages.append(page)
    return pages


def items(fetcher, today: date | None = None) -> Found:
    today = today or date.today()
    home = fetcher.request("GET", parse.SITE, timeout=120).content
    kinds = board_kinds(fetcher, home)
    boards = parse.boards(fetcher.request("GET", parse.ALL, timeout=120).content)
    out, seen, counts = Found(), {}, Counter()
    for board_id, name in boards:
        kind = kinds.get(board_id)
        place = board_place(name, kind)
        counts[f"boards: {kind or 'unknown kind'}"] += 1
        counts["boards tied to a place"] += place is not None
        found = 0
        for page in board_pages(fetcher, board_id, today):
            for meeting in parse.meetings(page):
                key = meeting["meeting_id"]
                if key in seen:
                    continue
                seen[key] = board_id
                found += 1
                data = {**meeting, "board_id": board_id, "board": name, "board_kind": kind}
                files = [d for d in meeting["documents"] if d["type"] not in NOT_DOWNLOADED]
                docs = [DocumentLink(d["url"], metadata={k: d[k] for k in
                                                         ("type", "description", "uploaded")})
                        for d in files]  # fmt: skip
                where = [STATE, *([place] if place else [])]
                out.append(Item(key=key, data=data, source_url=parse.board_url(board_id),
                                documents=docs, places=where))  # fmt: skip
        counts["boards with meetings"] += found > 0
    counts["meetings"] = len(out)
    counts["files"] = sum(len(i.data["documents"]) for i in out)
    out.checks = {"found": dict(counts),
                  "boards": len(boards)}  # fmt: skip
    return out


SOURCE = Source(
    slug="local-government-meetings", items=items, ingest="documents",
    name="Local government meetings", dataset="local-government-meetings",
    publisher="South Dakota Bureau of Information and Telecommunications",
    base_url=parse.SITE,
    description="Meetings of South Dakota's cities, counties and school boards posted on the "
                "state's Local Government Boards and Commissions portal, with agendas, packets, "
                "minutes and recordings.",
)  # fmt: skip
