"""Communication expenditure statements filed with the Secretary of State: the portal's list
since June 2018 (sdcfr.sdsos.gov), and the PDFs on the 2014–2018 pages before it.

Filed statements are keyed by their document URL; archived PDFs by year and file name,
since the site moves them between folders. Each statement's
filing is its document; what's in it is read later (step 4).
"""

from __future__ import annotations

from datetime import date
from pathlib import PurePosixPath
from urllib.parse import quote, unquote, urljoin, urlsplit

from entities.mentions import from_text
from records.sources import DocumentLink, Item, Source, _sdcfr
from records.sources._tables import document, text

from .schema import Statement

SEARCH = f"{_sdcfr.SITE}/Search/FiledCommunicationExpenditures.aspx"
GRID = "ctl00_MainContent_rgCommExList"
COLUMNS = ["Name of Person or Entity Making the Communication", "ID", "DocID",
           "Name Mentioned or Identified in Communication", "Communication Type & Description",
           "Date of Communication", "Date Filed"]  # fmt: skip
YEAR_PAGE = "https://sdsos.gov/elections-voting/campaign-finance/{}_Independent_expenditures.aspx"
YEARS = (2014, 2016, 2017, 2018)
STATE = "0400000US46"


def filed(fetcher) -> list[Item]:
    out = []
    month = date.today().strftime("%Y-%m")
    for cells in _sdcfr.grid(fetcher, SEARCH, GRID, COLUMNS, store_as=f"sdcfr/{month}"):
        values = [text(c) or None for c in cells[: len(COLUMNS)]]
        filer, fid, doc_id, mentioned, kind, when, filed_on = values
        url = f"{_sdcfr.SITE}/Document.aspx?DocumentID={doc_id}"
        s = Statement(system="sdcfr", filer_name=filer, filer_id=fid, document_id=doc_id,
                      name_mentioned=mentioned, communication=kind, communication_date=when,
                      date_filed=filed_on, year=None, title=None)  # fmt: skip
        doc = DocumentLink(url, metadata={"label": "Statement"})
        out.append(Item(key=url, data=s.model_dump(), source_url=url, places=[STATE],
                        documents=[doc]))  # fmt: skip
    return out


def archived(fetcher) -> list[Item]:
    """The PDFs on the 2014–2018 pages (filings are under ../assets/; the rest of the page's
    links are site navigation)."""
    out = []
    for year in YEARS:
        page = YEAR_PAGE.format(year)
        for a in document(fetcher.request("GET", page).content).iter("a"):
            href = (a.get("href") or "").strip()
            if not (href.lower().endswith(".pdf") and href.startswith("../assets/")):
                continue
            url = quote(urljoin(page, href), safe=":/?#[]@!$&'()*+,;=%~")
            s = Statement(system="archive", filer_name=None, filer_id=None, document_id=None,
                          name_mentioned=None, communication=None, communication_date=None,
                          date_filed=None, year=year, title=text(a) or None)  # fmt: skip
            doc = DocumentLink(url, metadata={"label": "Statement"})
            out.append(Item(key=archive_key(year, url), data=s.model_dump(), source_url=page,
                            places=[STATE], documents=[doc]))  # fmt: skip
    return out


def archive_key(year: int, url: str) -> str:
    """An archived PDF by year and file name: the site moves its files between folders
    (assets/X.pdf → assets/Archive/Prior to 2026/X.pdf)."""
    return f"{year} {unquote(PurePosixPath(urlsplit(url).path).name)}"


def items(fetcher) -> list[Item]:
    return filed(fetcher) + archived(fetcher)


def mentions(record) -> list:
    d = record.data
    if not d.get("filer_name"):
        return []
    return from_text(d["filer_name"], dataset=record.recordset.dataset, record_key=str(record),
                     field="filer_name", role="spender", url=record.source_url,
                     context={"mentioned": d["name_mentioned"]})  # fmt: skip


def from_legacy(data: dict) -> dict:
    if "filer_name" not in data:  # an archived PDF: PRL kept the year
        return {"year": data.get("year")}
    return {"filer_name": data.get("filer_name"), "filer_id": data.get("id"),
            "document_id": data.get("doc_id"), "name_mentioned": data.get("name_mentioned"),
            "communication": data.get("communication_type"),
            "communication_date": data.get("communication_date"),
            "date_filed": data.get("date_filed")}  # fmt: skip


def legacy_key(record) -> str:
    if "sdcfr" in record.key:
        return record.key
    return archive_key(record.data.get("year"), record.key)


SOURCE = Source(slug="communication-expenditures", items=items, mentions=mentions,
                categories={"statement": "A communication expenditure statement"},
                from_legacy=from_legacy, legacy_key=legacy_key)  # fmt: skip
