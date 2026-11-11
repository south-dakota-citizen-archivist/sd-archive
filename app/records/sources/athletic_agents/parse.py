"""The registrations table → rows. Pure: no fetching, no database."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urljoin

import lxml.html

from sddocs.clean import parse_date

from .schema import Registration

COLUMNS = ["Athlete Agent Name", "Effective Date", "Expiration Date"]


class LayoutChanged(ValueError):
    """The table isn't laid out the way the parser expects."""


@dataclass
class Row:
    registration: Registration
    form_url: str | None


def _text(cell) -> str:
    return " ".join(cell.text_content().split())


def rows(html: str | bytes, base_url: str) -> list[Row]:
    """Every registration in the table, in page order. Raises LayoutChanged rather than skip a
    row it can't read."""
    tables = lxml.html.fromstring(html).cssselect("table")
    if len(tables) != 1:
        raise LayoutChanged(f"expected one table, found {len(tables)}")
    trs = tables[0].cssselect("tr")
    if [_text(td) for td in trs[0].findall("td")] != COLUMNS:
        raise LayoutChanged(f"columns are {[_text(td) for td in trs[0].findall('td')]}")
    out = []
    for n, tr in enumerate(trs[1:], start=2):
        cells = tr.findall("td")
        if not any(_text(c) for c in cells):
            continue  # an empty spacer row
        if len(cells) != len(COLUMNS):
            raise LayoutChanged(f"row {n} has {len(cells)} cells")
        name, effective, expiration = (_text(c) for c in cells)
        if not name:
            raise LayoutChanged(f"row {n} has no name")
        link = cells[0].find(".//a[@href]")
        out.append(Row(
            Registration(agent_name=name, effective_date=parse_date(effective) if effective
                         else None, expiration_date=parse_date(expiration) if expiration
                         else None, effective_date_raw=effective,
                         expiration_date_raw=expiration),
            urljoin(base_url, link.get("href").strip()) if link is not None else None,
        ))  # fmt: skip
    return out
