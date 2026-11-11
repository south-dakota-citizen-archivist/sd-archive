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
    """The form's fields to post back, without the portal's login box."""
    return {i.get("name"): i.get("value") or "" for i in root.iter("input")
            if i.get("name") and i.get("type") in ("hidden", "text", None)
            and not i.get("name").endswith(("txtUserN", "txtPassword"))}  # fmt: skip


def grid(fetcher, url: str, grid_id: str, columns: list[str],
         store_as: str = "") -> Iterator[list]:  # fmt: skip
    """The cells of every row of the grid, page by page. columns: the header, which must
    match. store_as: a store folder for each page's HTML (page-0001.html, …)."""
    html = fetcher.request("GET", url).content
    seen, page = 0, 1
    unique = grid_id.replace("_", "$")
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
        for tr in rows:
            yield tr.findall("td")
        seen += len(rows)
        counted = COUNT.search(" ".join(root.text_content().split()))
        total, pages = (int(counted[1]), int(counted[2])) if counted else (seen, 1)
        if page >= pages:
            if seen != total:
                raise LayoutChanged(f"{url}: read {seen} rows of {total}")
            return
        data = {**_fields(root), "__EVENTTARGET": unique,
                "__EVENTARGUMENT": f"FireCommand:{unique}$ctl00;Page;next"}  # fmt: skip
        html = fetcher.request("POST", url, data=data).content
        page += 1
