"""The state's public phone directory (sd.gov's phone book, a ServiceNow portal).

Read over its JSON API: the page data gives the agency filter's 32 agencies; each agency's
entries are read ten to a page, then the entries in no listed agency, and the total is
checked against the directory's own count. Keyed by name and phone (the directory shows no
ID of its own); an entry listed under two agencies is one record with both.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date

from entities.mentions import from_text
from records.sources import Item, Source
from records.sources._tables import LayoutChanged

from . import parse
from .schema import Entry

SITE = "https://www.sd.gov"
PAGE_URL = f"{SITE}/cs?id=phone_book"
PORTAL = "6cd61a81dbb0b810b2fb93d4f3961931"
PAGE_API = (
    f"{SITE}/api/now/sp/page?id=phone_book&portal_id={PORTAL}&request_uri=%2Fcs%3Fid%3Dphone_book"
)
INSTANCE = "b39b607c1b7e95501c4fa97ae54bcb9d"
RESULTS_API = f"{SITE}/api/now/sp/rectangle/{INSTANCE}?id=phone_book"
STATE = "0400000US46"
BASE = "active=true^is_public=true"
REQUEST = {
    "instance_id": INSTANCE, "hidden_template": "public-phone-book-hidden-template",
    "footer_template": "footer-default", "body_template": "public-phone-book-body",
    "isSocialMediaPage": False, "action": "page_change", "sessionRotationTrigger": True,
    "plural": "Phone Book Entries", "window_start": 0, "window_end": 10, "show": True,
    "order_direction": "asc", "display_field": "name",
    "filterObj": {"table": "x_1234_phone_book_phone_book", "display_field": "name",
                  "order_direction": "asc", "fields": "agency,phone_number", "show": True},
}  # fmt: skip


def key(name: str, phone: str | None) -> str:
    return f"{name} | {phone or 'no phone'}"


class Session:
    """The token and headers the portal's own pages send. Responses are stored by month
    (raw/<month>/…), so a stopped run reuses what it already read."""

    def __init__(self, fetcher, month: str | None = None):
        self.fetcher = fetcher
        self.month = month or date.today().strftime("%Y-%m")
        page = fetcher.request("GET", PAGE_URL).text
        token = re.search(r"g_ck\s*=\s*['\"]([^'\"]+)['\"]", page)
        if not token:
            raise LayoutChanged("no session token (g_ck) on the phone book page")
        self.headers = {"Accept": "application/json", "Referer": PAGE_URL, "x-portal": PORTAL,
                        "X-UserToken": token[1], "X-Requested-With": "XMLHttpRequest"}  # fmt: skip

    def page_data(self) -> dict:
        name = f"{self.month}/page.json"
        return json.loads(self.fetcher.fetch(PAGE_API, name, headers=self.headers))

    def results(self, query: str, page: int) -> dict:
        flt = {**REQUEST["filterObj"], "filter": f"{query}^ORDERBYlast_name"}
        body = {**REQUEST, "page": page, "filterObj": flt}
        headers = {**self.headers, "Content-Type": "application/json"}
        group = hashlib.sha256(query.encode()).hexdigest()[:12]
        name = f"{self.month}/results/{group}/{page:04d}.json"
        return json.loads(self.fetcher.fetch(RESULTS_API, name, data=json.dumps(body).encode(),
                                             headers=headers))  # fmt: skip


def _all_pages(session: Session, query: str) -> tuple[list[dict], int]:
    first = session.results(query, 0)
    data = first["result"]["data"]
    found = parse.entries(first)
    for page in range(1, data["num_pages"]):
        found += parse.entries(session.results(query, page))
    if len(found) != data["row_count"]:
        raise LayoutChanged(f"{query}: read {len(found)} entries of {data['row_count']}")
    return found, data["row_count"]


def items(fetcher) -> list[Item]:
    session = Session(fetcher)
    agencies = parse.agencies(session.page_data())
    groups = [(f"{BASE}^agencyIN{aid}", aid, label) for aid, label in agencies]
    groups.append((f"{BASE}^agencyNOT IN{','.join(a for a, _ in agencies)}^ORagencyISEMPTY",
                   None, None))  # fmt: skip
    total = session.results(BASE, 0)["result"]["data"]["row_count"]
    by_key: dict[str, dict] = {}
    listed = 0
    for query, aid, label in groups:
        found, count = _all_pages(session, query)
        listed += count
        for e in found:
            entry = by_key.setdefault(key(e["full_name"], e["phone"]),
                                      {**e, "agencies": [], "agency_ids": []})  # fmt: skip
            if aid and aid not in entry["agency_ids"]:
                entry["agencies"].append(label)
                entry["agency_ids"].append(aid)
    if listed < total:
        raise LayoutChanged(f"the agencies list {listed} entries; the directory has {total}")
    out = []
    for k, e in by_key.items():
        given, family = parse.person_parts(e["full_name"])
        entry = Entry(**e, given_name=given, family_name=family)
        out.append(Item(key=k, data=entry.model_dump(mode="json"), source_url=PAGE_URL,
                        places=[STATE]))  # fmt: skip
    return out


def mentions(record) -> list:
    """The person an entry names; none for an office or unit."""
    d = record.data
    if parse.is_office(d["full_name"]):
        return []
    return from_text(d["full_name"], dataset=record.recordset.dataset, record_key=str(record),
                     field="full_name", role="state employee", order="given_first",
                     url=record.source_url, context={"agencies": d["agencies"]})  # fmt: skip


def from_legacy(data: dict) -> dict:
    """The liberator kept one agency per entry (the first that listed it)."""
    return {"full_name": data.get("full_name"), "phone": data.get("phone"),
            "agencies": [data["agency"]] if data.get("agency") else []}  # fmt: skip


def legacy_key(record) -> str:
    return key(record.data.get("full_name"), record.data.get("phone"))


SOURCE = Source(slug="state-employee-directory", items=items, mentions=mentions,
                from_legacy=from_legacy, legacy_key=legacy_key)  # fmt: skip
