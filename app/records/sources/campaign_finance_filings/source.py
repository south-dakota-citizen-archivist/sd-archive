"""Campaign finance committees and their filings, from sdcfr.sdsos.gov (the portal since
2018). The committee search (by name, blank: every committee) lists them; each committee's
page gives its officers and every filing, scanned or filed online. One record per committee
page (cid and rid), its filings the record's documents.

The record set also holds filings from the Secretary of State's older search system, which
this source doesn't read (covers()); those wait for that system's port.
"""

from __future__ import annotations

from datetime import date

from entities.mentions import from_text
from records.sources import DocumentLink, Item, Source, _sdcfr
from records.sources._places import city_place
from records.sources._tables import text

from . import parse

SEARCH = f"{_sdcfr.SITE}/Search/Search.aspx"
GRID = "ctl00_MainContent_rgSearchCommType_Name"
COLUMNS = ["Committee Name", "Committee Type", "Candidate", "Ballot Question", "Chair",
           "Treasurer", "Status", "CFR Username"]  # fmt: skip
STATE = "0400000US46"
CHOICE = "ctl00$MainContent$rblCommittee"


def committee_search(fetcher) -> bytes:
    """The search's first page of committees: choose "by name", then search with it blank."""
    html = fetcher.request("GET", SEARCH).content
    html = fetcher.request("POST", SEARCH, data={
        **_sdcfr.form_fields(html), "__EVENTTARGET": f"{CHOICE}$0", "__EVENTARGUMENT": "",
        CHOICE: "Name"}).content  # fmt: skip
    return fetcher.request("POST", SEARCH, data={
        **_sdcfr.form_fields(html), CHOICE: "Name", "__EVENTARGUMENT": "",
        "__EVENTTARGET": "ctl00$MainContent$btnSearchCommType_Name"}).content  # fmt: skip


def committees(fetcher, month: str) -> list[dict]:
    """Every committee the search lists, with its URL and the grid's own columns."""
    out = {}
    start = committee_search(fetcher)
    for cells in _sdcfr.grid(fetcher, SEARCH, GRID, COLUMNS, store_as=f"search/{month}",
                             start=start):  # fmt: skip
        link = next((a.get("href") for td in cells for a in td.iter("a")
                     if "SearchResults.aspx" in (a.get("href") or "")), None)  # fmt: skip
        if not link:
            continue
        url = _sdcfr.SITE + "/Search/" + link.split("Search/")[-1]
        values = [text(c) or None for c in cells[: len(COLUMNS)]]
        out[url] = {"url": url, "ballot_question": values[3], "cfr_username": values[7]}
    return list(out.values())


def page(fetcher, url: str, month: str) -> bytes:
    cid_rid = url.split("?", 1)[1].replace("&", "-").replace("=", "")
    if getattr(fetcher, "store", None) is not None:
        return fetcher.fetch(url, f"committees/{month}/{cid_rid}.html")
    return fetcher.request("GET", url).content


def items(fetcher) -> list[Item]:
    month = date.today().strftime("%Y-%m")
    out = []
    for c in committees(fetcher, month):
        info, filings = parse.committee(page(fetcher, c["url"], month), c["url"])
        data = {**info, "ballot_question": c["ballot_question"],
                "cfr_username": c["cfr_username"], "filings": len(filings)}  # fmt: skip
        city = city_place(_city(info["address"]))
        docs = [DocumentLink(f["url"], metadata={k: v for k, v in f.items() if k != "url"})
                for f in filings]  # fmt: skip
        out.append(Item(key=c["url"], data=data, source_url=c["url"], documents=docs,
                        places=[STATE, *([city] if city else [])]))  # fmt: skip
    return out


def _city(address: str | None) -> str | None:
    """'4314 North Glenview Place, Rapid City SD, 57702' → 'Rapid City' (South Dakota only)."""
    if not address or " SD" not in address:
        return None
    before = address.rsplit(" SD", 1)[0]
    return before.rsplit(",", 1)[-1].strip() or None


def mentions(record) -> list:
    d, out = record.data, []
    for field, role in (("committee_name", "committee"), ("candidate_name", "candidate"),
                        ("chair_name", "chair"), ("treasurer_name", "treasurer")):  # fmt: skip
        if d.get(field):
            out += from_text(d[field], dataset=record.recordset.dataset, record_key=str(record),
                             field=field, role=role, order="given_first",
                             url=record.source_url, start=len(out))  # fmt: skip
    return out


def from_legacy(data: dict) -> dict:
    return {"committee_name": data.get("committee_name"),
            "candidate_name": data.get("candidate_name"), "status": data.get("committee_status"),
            "committee_type": data.get("committee_type"), "address": data.get("committee_address"),
            "phone": data.get("committee_phone"), "chair_name": data.get("chair_name"),
            "treasurer_name": data.get("treasurer_name")}  # fmt: skip


SOURCE = Source(slug="campaign-finance-filings", items=items, mentions=mentions,
                covers=lambda key: "sdcfr.sdsos.gov" in key, from_legacy=from_legacy)  # fmt: skip
