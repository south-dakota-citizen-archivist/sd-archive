"""The Department of Corrections' offender locator (docadultlookup.sd.gov): search results
(DOC number, name, and the link to each person's page) and the person's page."""

from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urljoin

from records.sources._tables import LayoutChanged, document, text

URL = "https://docadultlookup.sd.gov/adult/lookup/"
GRID = "grvList"


def detail_url(detail_id: str) -> str:
    return urljoin(URL, f"details/?id={detail_id}")


def results(html: bytes | str) -> list[dict]:
    """The people on one page of search results."""
    root = document(html)
    table = root.cssselect(f"table#{GRID}")
    if not table:
        return []
    out = []
    for tr in table[0].iter("tr"):
        cells = [td for td in tr if td.tag == "td"]
        link = cells[0].find(".//a[@href]") if cells else None
        if link is None or len(cells) < 4 or "details/" not in link.get("href"):
            continue  # the header, or the pager row
        m = re.search(r"details/\?id=([^&]+)", link.get("href"))
        values = [text(c) for c in cells]
        out.append({"doc_number": values[0], "last_name": values[1], "first_name": values[2],
                    "middle_initial": values[3] if len(values) > 3 else "",
                    "detail_id": m.group(1)})  # fmt: skip
    return out


def next_page(html: bytes | str, page: int) -> str | None:
    """The postback argument for the page after `page`, if the pager offers one."""
    arg = f"Page${page + 1}"
    raw = html if isinstance(html, str) else html.decode("utf-8", "replace")
    return arg if f"'{GRID}','{arg}'" in raw else None


def _date(value: str) -> str | None:
    try:
        return datetime.strptime(value, "%m/%d/%Y").date().isoformat()
    except ValueError:
        return None


def detail(html: bytes | str) -> dict:
    """A person's page: every labeled value (by its label, as published), and the dated
    line under it ("Discharged", or whatever that line is labeled) with its date."""
    root = document(html)
    name = root.cssselect("#lblMainName")
    if not name:
        raise LayoutChanged("not a person's page")
    out: dict = {"name": text(name[0]), "fields": {}}
    for label in root.cssselect("label[for]"):
        value = root.cssselect(f"span#{label.get('for')}")
        if value:
            out["fields"][text(label)] = text(value[0])
    alias = root.cssselect("#lblAlias")
    if alias:  # one name per line
        out["aliases"] = [" ".join(t.split()) for t in alias[0].itertext() if t.strip()]
    address = root.cssselect("#lblCurrLocAddr")
    lines = [" ".join(t.split()) for t in address[0].itertext() if t.strip()] if address else []
    if lines:  # "1600 North Drive", "Sioux Falls, SD 57117"
        out["facility_address"] = lines
        m = re.match(r"^(.+?),\s*([A-Z]{2})\b", lines[-1])
        out["facility_city"], out["facility_state"] = (
            (m.group(1), m.group(2)) if m else (None, None)
        )
    event = root.cssselect("#lblDischarge")
    when = root.cssselect("#lblDischargeDate")
    if event and when:
        out["event"] = text(event[0])
        out["event_date_raw"] = text(when[0])
        out["event_date"] = _date(text(when[0]))
    return out
