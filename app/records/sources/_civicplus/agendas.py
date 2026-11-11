"""The Agenda Center (/AgendaCenter): one category per board, each with a list of meetings
by year (the year lists are fetched as the page fetches them, from UpdateCategoryList).
Each meeting has an agenda, and may have a packet, minutes, an HTML agenda and video."""

from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urljoin

from records.sources._tables import document, text

_YEAR = re.compile(r"changeYear\((\d{4}),\s*(\d+),")


def categories(html: str | bytes) -> dict[str, str]:
    """Category ID → board name."""
    root = document(html)
    out = {}
    for h2 in root.cssselect("h2[aria-controls^='category-panel-']"):
        out[h2.get("aria-controls").removeprefix("category-panel-")] = text(h2)
    return out


def years(html: str | bytes, category: str) -> list[int]:
    raw = html if isinstance(html, str) else html.decode("utf-8", "replace")
    return sorted({int(y) for y, c in _YEAR.findall(raw) if c == category}, reverse=True)


def meetings(html: str | bytes, base_url: str) -> list[dict]:
    """The meetings in a category's year list."""
    root = document(html)
    out = []
    for row in root.cssselect("tr.catAgendaRow"):
        heading = row.cssselect("h3")
        anchor = row.cssselect("h3 a[name]")
        if not heading or not anchor:
            continue
        m = re.match(r"_(\d{8})-(\d+)$", anchor[0].get("name"))
        if not m:
            continue
        date = datetime.strptime(m.group(1), "%m%d%Y").date().isoformat()
        posted = re.search(r"Posted\s+(.+)$", text(heading[0]))
        title = row.cssselect("td p a")
        links: dict[str, str] = {}
        for a in row.cssselect("a[href]"):
            href = a.get("href")
            if "/ViewFile/Minutes/" in href:
                links["minutes"] = urljoin(base_url, href)
            elif "packet=true" in href:
                links["packet"] = urljoin(base_url, href)
            elif "/ViewFile/Agenda/" in href and "html=true" in href:
                links["agenda_html"] = urljoin(base_url, href)
            elif "/ViewFile/Agenda/" in href:
                links.setdefault("agenda", urljoin(base_url, href))
        media = [a.get("href") for a in row.cssselect("td.media a[href]")]
        out.append({"agenda_id": m.group(2), "date": date,
                    "title": text(title[0]) if title else "",
                    "posted": posted.group(1) if posted else None,
                    "files": links, "media": media})  # fmt: skip
    return out
