"""sdcfr.sdsos.gov, the Secretary of State's campaign finance portal: its search pages are
Telerik RadGrids paged by postback. grid() reads every page over HTTP (the grid's own "next
page" command) and checks the rows against the grid's "N items in P pages"."""

from __future__ import annotations

import re
from collections.abc import Iterator

from records.sources._tables import LayoutChanged, document, text

SITE = "https://sdcfr.sdsos.gov"
COUNT = re.compile(r"(\d+) items? in (\d+) pages?")


def _fields(root) -> dict[str, str]:
    """The form's state to post back, as a browser would: hidden and text fields, checked
    radios and checkboxes, selected options; without the portal's login box."""
    out = {}
    for i in root.iter("input"):
        name, kind = i.get("name"), (i.get("type") or "text").lower()
        if not name or name.endswith(("txtUserN", "txtPassword")):
            continue
        if kind in ("hidden", "text") or (kind in ("radio", "checkbox") and i.get("checked")
                                          is not None):  # fmt: skip
            out[name] = i.get("value") or ("on" if kind == "checkbox" else "")
    for sel in root.iter("select"):
        chosen = sel.xpath(".//option[@selected]") or sel.xpath(".//option")[:1]
        if sel.get("name") and chosen:
            out[sel.get("name")] = chosen[0].get("value") or ""
    return out


def _unique_id(table, grid_id: str) -> str:
    """The grid's postback name, from its own pager links (an underscore in the ID can be
    part of the name: rgSearchCommType_Name)."""
    for el in table.iter("a", "input"):
        found = re.search(
            r"__doPostBack\('([^']+?)\$ctl00\$", el.get("href") or el.get("onclick") or ""
        )
        if found:
            return found[1]
    return grid_id.replace("_", "$")


def form_fields(html: bytes) -> dict[str, str]:
    return _fields(document(html))


def grid(fetcher, url: str, grid_id: str, columns: list[str], store_as: str = "",
         start: bytes | None = None) -> Iterator[list]:  # fmt: skip
    """The cells of every row of the grid, page by page. columns: the header, which must
    match. store_as: a store folder for each page's HTML (page-0001.html, …). start: the
    first page, when the grid only appears after a search (otherwise it's fetched)."""
    html = start if start is not None else fetcher.request("GET", url).content
    seen, page, previous = 0, 1, []
    while True:
        if store_as and getattr(fetcher, "store", None) is not None:
            fetcher.store.save(f"{store_as}/page-{page:04d}.html", html, url)
        root = document(html)
        tables = root.cssselect(f"table#{grid_id}_ctl00")
        if not tables:
            raise LayoutChanged(f"{url}: no grid {grid_id}")
        header = [text(th) for th in tables[0].cssselect("thead th")]
        if header[: len(columns)] != columns:
            raise LayoutChanged(f"{url}: columns are {header}")
        rows = tables[0].cssselect("tr.rgRow, tr.rgAltRow")
        signature = [text(tr) for tr in rows]
        if page > 1 and rows and signature == previous:
            raise LayoutChanged(
                f"{url}: page {page} repeats page {page - 1}; paging didn't advance"
            )
        previous = signature
        for tr in rows:
            yield tr.findall("td")
        seen += len(rows)
        counted = COUNT.search(" ".join(root.text_content().split()))
        total, pages = (int(counted[1]), int(counted[2])) if counted else (seen, 1)
        if page >= pages:
            if seen != total:
                raise LayoutChanged(f"{url}: read {seen} rows of {total}")
            return
        unique = _unique_id(tables[0], grid_id)
        data = {**_fields(root), "__EVENTTARGET": unique,
                "__EVENTARGUMENT": f"FireCommand:{unique}$ctl00;Page;next"}  # fmt: skip
        html = fetcher.request("POST", url, data=data).content
        page += 1
