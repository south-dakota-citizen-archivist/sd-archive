"""The Local Government Boards and Commissions portal (sdlocalgov.appssd.sd.gov): the boards
(cities, counties, school boards) and the meetings each posts, with their files and links."""

from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urljoin

from records.sources._tables import LayoutChanged, document, text

SITE = "https://sdlocalgov.appssd.sd.gov/"
ALL = SITE + "SearchResults.aspx?Letter=ALL"
TYPES = {"3": "city", "2": "county", "1": "school board"}  # the site's search-by-type values


def board_url(board_id: str) -> str:
    return f"{SITE}Meetings.aspx?BoardID={board_id}"


def boards(html: bytes | str) -> list[tuple[str, str]]:
    """(board ID, name) for each board a search result lists."""
    root = document(html)
    table = root.cssselect("table#gvResults")
    if not table:
        if "noresults" in (html if isinstance(html, str) else html.decode("utf-8", "replace")):
            return []
        raise LayoutChanged("no results table")
    out = []
    for a in table[0].iter("a"):
        m = re.search(r"Meetings\.aspx\?BoardID=(\d+)", a.get("href") or "")
        if m:
            out.append((m.group(1), text(a)))
    return out


def years(html: bytes | str) -> list[str]:
    """The years a board's archive offers."""
    select = document(html).cssselect("select#ddlArchiveYear")
    return [o.get("value") for o in select[0].iter("option")] if select else []


def _when(value: str) -> str | None:
    value = " ".join(value.split())
    for fmt in ("%m/%d/%Y %I:%M %p", "%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y"):
        try:
            return datetime.strptime(value, fmt).isoformat()
        except ValueError:
            continue
    return None


def _labeled(body) -> dict[str, str]:
    """The card's "Label: value" lines (the value is the text up to the next <strong>)."""
    out = {}
    for strong in body.iter("strong"):
        label = text(strong).rstrip(":")
        value = []
        node = strong
        while (node := node.getnext()) is not None and node.tag != "strong":
            if node.tag in ("div", "table"):
                break
            value.append(node.text_content())
            if node.tail:
                value.append(node.tail)
        tail = (strong.tail or "") + "".join(value)
        out[label] = " ".join(tail.split())
    return out


def meetings(html: bytes | str) -> list[dict]:
    """Every meeting on a board's page (upcoming, and the archive's chosen year)."""
    root = document(html)
    if not root.cssselect("#meetingsContain"):
        raise LayoutChanged("no meetings section")
    out = []
    for card in root.cssselect("div.card"):
        header = card.cssselect(".card-header")
        if not header:
            continue
        m = re.match(r"heading(Upcoming|Archive)(\d+)$", header[0].get("id") or "")
        if not m:
            continue
        date, _, body_name = text(header[0]).partition("|")
        body = card.cssselect(".card-body")[0]
        fields = _labeled(body)
        documents, links = [], []
        for table in body.iter("table"):
            tid = table.get("id") or ""
            for tr in table.iter("tr"):
                cells = [c for c in tr if c.tag == "td"]
                a = cells[0].find(".//a[@href]") if cells else None
                if a is None:
                    continue
                url = urljoin(SITE, a.get("href"))
                if "gvDocuments" in tid:
                    documents.append({
                        "description": text(a), "url": url,
                        "file_id": (re.search(r"FileID=(\d+)", url) or [None, None])[1],
                        "type": text(cells[1]) if len(cells) > 1 else "",
                        "uploaded": _when(text(cells[2])) if len(cells) > 2 else None,
                    })  # fmt: skip
                elif "gvLinks" in tid:
                    links.append({"text": text(a), "url": url})
        out.append({
            "meeting_id": m.group(2), "date": (_when(date.strip()) or "")[:10] or None,
            "body": body_name.strip(),
            "start": _when(fields.get("Start Date/Time", "")),
            "end": _when(fields.get("End Date/Time", "")),
            "time_zone": fields.get("Time Zone") or None,
            "description": fields.get("Description") or None,
            "location": fields.get("Location") or None,
            "documents": documents, "links": links,
        })  # fmt: skip
    return out
