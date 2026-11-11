"""Members of state boards and commissions, from boardsandcommissions.sd.gov: the A–Z board
list, then each board's member page.

One row per appointment, keyed by board, member and term end (a reappointment is a new
record). The site lists one ex-officio member twice; identical rows collapse, and rows that
share a key but differ are kept under a numbered key.
"""

from __future__ import annotations

import logging

from entities.mentions import from_text
from records.sources import Item, Source

from . import parse

logger = logging.getLogger(__name__)

SITE = "https://boardsandcommissions.sd.gov/"
LIST_URL = SITE + "SearchResults.aspx?Letter=ALL"
STATE = "0400000US46"


def board_url(board_id: int) -> str:
    return f"{SITE}boardmembers.aspx?BoardID={board_id}"


def key(member) -> str:
    term = member.term_end.isoformat() if member.term_end else (member.term_end_raw or "no term")
    return f"{member.board_id} {member.name} {term}"


def items(fetcher) -> list[Item]:
    out: dict[str, Item] = {}
    for board_id in parse.board_ids(fetcher.request("GET", LIST_URL).content):
        url = board_url(board_id)
        for member in parse.members(fetcher.request("GET", url).content, board_id, SITE):
            places = [STATE, *([member.city_place] if member.city_place else [])]
            item = Item(key=key(member), data=member.model_dump(mode="json"), source_url=url,
                        places=places)  # fmt: skip
            base, n = item.key, 1
            while item.key in out:
                if out[item.key].data == item.data:
                    break  # the same row twice
                n += 1
                item.key = f"{base} ({n})"
            else:
                out[item.key] = item
                continue
            logger.info("board %s lists %s twice", board_id, member.name)
    return list(out.values())


def mentions(record) -> list:
    d = record.data
    return from_text(d["name"], dataset=record.recordset.dataset, record_key=str(record),
                     field="name", role="board member", order="given_first",
                     url=record.source_url, context={"board": d["board_name"],
                                                     "position": d["position"]})  # fmt: skip


def from_legacy(data: dict) -> dict:
    """The liberator's rows: no board ID (it was in its key), the term end as ISO."""
    return {k: data.get(k) for k in (
        "board_name", "board_website", "board_phone", "board_email", "agency_affiliation",
        "agency_website", "name", "position", "city", "term_end", "party", "bio", "picture_url",
    )}  # fmt: skip


def legacy_key(record) -> str:
    from urllib.parse import parse_qs, urlsplit

    q = parse_qs(urlsplit(record.key).query)
    term = record.data.get("term_end") or "no term"
    return f"{q.get('BoardID', ['?'])[0]} {record.data.get('name')} {term}"


SOURCE = Source(slug="boards-commissions-members", items=items, mentions=mentions,
                from_legacy=from_legacy, legacy_key=legacy_key)  # fmt: skip
