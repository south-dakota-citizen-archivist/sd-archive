"""Executive orders filed with the Secretary of State, one record per filed document: the
order number, the date filed and the title.

The orders come two ways, and both are kept: the search lists every order back to 1963 as a
scanned document (keyed by its DocGuid; the search won't list everything at once, so it's run
for the years starting with 1 and with 2), and the page's own tables list the orders since
2020 with a PDF of each (keyed by the PDF's URL, as written). An order since 2020 is in both. Order
numbers aren't unique (a dozen repeat), so they aren't the key.
"""

from __future__ import annotations

from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit

from records.sources import DocumentLink, Found, Item, Source
from records.sources._sos_search import (
    STATE,
    Column,
    LayoutChanged,
    SearchSpec,
    from_legacy_for,
    legacy_key,
    row_data,
    rows,
    search,
)

URL = "https://sdsos.gov/general-information/executive-actions/executive-orders/search/Default.aspx"
YEAR_FILED = "FD_A723A07D"
SPEC = SearchSpec(
    slug="executive-orders", url=URL,
    columns=[Column("Order Number", "order_number"), Column("Date Filed", "date_filed", "date"),
             Column("Title", "title")],
    searches=[{YEAR_FILED: "1"}, {YEAR_FILED: "2"}],
)  # fmt: skip


def _url(href: str) -> str:
    parts = urlsplit(urljoin(URL, href))
    return urlunsplit(parts._replace(path=quote(unquote(parts.path))))


def tables(html: bytes) -> list[dict]:
    """The page's tables of orders since 2020: number, date filed, title and the PDF."""
    import lxml.html

    out = []
    root = lxml.html.fromstring(html)
    for table in root.xpath("//table[not(ancestor::*[@id='lbl_SearchInfo'])]"):
        trs = table.xpath(".//tr")
        head = (
            [" ".join(c.text_content().split()) for c in trs[0].xpath("./th|./td")] if trs else []
        )
        if head[:3] != ["Order Number", "Dated Filed", "Title"]:
            raise LayoutChanged(f"executive orders: a table headed {head}")
        for tr in trs[1:]:
            cells = [" ".join(c.text_content().split()) for c in tr.xpath("./td")]
            links = tr.xpath(".//a/@href")
            if cells and links:
                out.append({"cells": {"Order Number": cells[0], "Date Filed": cells[1],
                                      "Title": cells[2] if len(cells) > 2 else ""},
                            "url": _url(links[0]),
                            "files": [_url(h) for h in dict.fromkeys(links)]})  # fmt: skip
    return out


def items(fetcher) -> Found:
    out, seen, counts, pages = Found(), set(), {}, []
    for fields in SPEC.searches:
        html = search(fetcher, URL, fields)
        pages.append(html)
        _, found = rows(html, URL)
        counts[f"search {fields[YEAR_FILED]}"] = len(found)
        for row in found:
            data = row_data(SPEC, row)
            if data["doc_guid"] in seen:
                continue
            seen.add(data["doc_guid"])
            out.append(Item(key=data["doc_guid"], data=data, source_url=row["url"],
                            documents=[DocumentLink(row["url"], f"{data['doc_guid']}.pdf")],
                            places=[STATE]))  # fmt: skip
    # the year tables are on the search's own page too: no request of their own
    listed = tables(pages[0])
    counts["tables"] = len(listed)
    for row in listed:
        data = {k: v for k, v in row_data(SPEC, row).items() if k not in ("doc_guid", "cab_id")}
        key = unquote(row["url"])  # the PDF's URL as written (spaces and all)
        if key in seen:
            continue
        seen.add(key)
        # an order's row can link more than its PDF (2025-08: the sets of orders it rescinds)
        out.append(Item(key=key, data=data, source_url=row["url"],
                        documents=[DocumentLink(u) for u in row["files"]],
                        places=[STATE]))  # fmt: skip
    out.checks = {"listed": counts, "documents": len(out)}
    return out


SOURCE = Source(
    slug="executive-orders", items=items, name="Executive Orders", dataset="executive-orders",
    publisher="South Dakota Secretary of State", base_url=URL,
    description="The governor's executive orders: number, date filed, title.",
    from_legacy=from_legacy_for(SPEC), legacy_key=legacy_key,
)  # fmt: skip
