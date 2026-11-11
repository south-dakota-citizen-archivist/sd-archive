"""Supplemental campaign finance statements (SDCL 12-27-22: contributions or expenditures of
$500 or more in the 20 days before an election, reported within 24 hours), from
sdcfr.sdsos.gov's list. One record per statement, keyed by its document ID (the link's
type parameter has changed: sf, then sup); the statement is its document."""

from __future__ import annotations

from datetime import date
from urllib.parse import parse_qs, urljoin, urlsplit

from entities.mentions import from_text
from records.sources import DocumentLink, Item, Source, _sdcfr
from records.sources._tables import text
from sddocs.clean import parse_date

SEARCH = f"{_sdcfr.SITE}/Search/FiledSupplementalStatements.aspx"
GRID = "ctl00_MainContent_rgSupplementalList"
COLUMNS = ["Committee Name", "ID", "DocID", "Date Submitted"]
STATE = "0400000US46"


def key(document_id: str) -> str:
    return f"{_sdcfr.SITE}/Document.aspx?DocumentID={document_id}"


def items(fetcher) -> list[Item]:
    month = date.today().strftime("%Y-%m")
    out = {}
    for cells in _sdcfr.grid(fetcher, SEARCH, GRID, COLUMNS, store_as=f"sdcfr/{month}"):
        name, filer_id, doc_id, submitted = (text(c) or None for c in cells[:4])
        link = next((a.get("href") for a in cells[0].getparent().iter("a")
                     if "Document.aspx" in (a.get("href") or "")), None)  # fmt: skip
        url = urljoin(SEARCH, link) if link else key(doc_id)
        on = parse_date(submitted) if submitted else None
        data = {"committee_name": name, "filer_id": filer_id, "document_id": doc_id,
                "date_submitted": submitted, "submitted_on": on.isoformat() if on else None,
                "form_type": parse_qs(urlsplit(url).query).get("type", [None])[0]}  # fmt: skip
        doc = DocumentLink(url, metadata={"label": "Statement"})
        out[key(doc_id)] = Item(key=key(doc_id), data=data, source_url=url, places=[STATE],
                                documents=[doc])  # fmt: skip
    return list(out.values())


def mentions(record) -> list:
    d = record.data
    if not d.get("committee_name"):
        return []
    return from_text(d["committee_name"], dataset=record.recordset.dataset,
                     record_key=str(record), field="committee_name", role="committee",
                     url=record.source_url)  # fmt: skip


def legacy_key(record) -> str:
    return key(record.data.get("document_id") or parse_qs(urlsplit(record.key).query)
               .get("DocumentID", [""])[0])  # fmt: skip


def from_legacy(data: dict) -> dict:
    return {"committee_name": data.get("committee_name"), "document_id": data.get("document_id"),
            "submitted_on": data.get("date_submitted")}  # fmt: skip


SOURCE = Source(slug="supplemental-campaign-finance", items=items, mentions=mentions,
                legacy_key=legacy_key, from_legacy=from_legacy)  # fmt: skip
