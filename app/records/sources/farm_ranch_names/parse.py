"""The registrations table → rows. Pure: no fetching, no database."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from urllib.parse import urljoin

import lxml.html

import sdplaces
from sddocs.clean import parse_date

from .schema import Registration

COLUMNS = ["Farm, Ranch, Home Name", "Owner(s)", "Date Registered", "County", "Image"]


class LayoutChanged(ValueError):
    """The table isn't laid out the way the parser expects."""


@dataclass
class Row:
    registration: Registration
    files: list[tuple[str, str]]  # (URL, link text): the certificate, and any amendment

    @property
    def certificate_url(self) -> str | None:
        return self.files[0][0] if self.files else None


def _lines(cell) -> list[str]:
    """The cell's text, one entry per line (the site separates names with <br>)."""
    return [" ".join(t.split()) for t in cell.itertext() if t.strip()]


def county_geoids(counties: list[str], on: date | None) -> list[str]:
    """Each county's GEOID, for the county in force on the date (Shannon before 2015)."""
    return [sdplaces.county(c, as_of=on).geoid for c in counties]


def rows(html: str | bytes, base_url: str) -> list[Row]:
    """Every registration in the table, in page order. Raises LayoutChanged rather than skip a
    row it can't read."""
    root = lxml.html.fromstring(html)
    tables = root.cssselect("table")
    if len(tables) != 1:
        raise LayoutChanged(f"expected one table, found {len(tables)}")
    trs = tables[0].cssselect("tr")
    header = [" ".join(th.text_content().split()) for th in trs[0].cssselect("th")]
    if header != COLUMNS:
        raise LayoutChanged(f"columns are {header}")
    out = []
    for n, tr in enumerate(trs[1:], start=2):
        cells = tr.findall("td")
        if len(cells) != len(COLUMNS):
            raise LayoutChanged(f"row {n} has {len(cells)} cells")
        name, *notes = _lines(cells[0]) or [""]
        if not name:
            raise LayoutChanged(f"row {n} has no name")
        raw_date = " ".join(cells[2].text_content().split())
        on = parse_date(raw_date) if raw_date else None
        counties = _lines(cells[3])
        files = [(urljoin(base_url, a.get("href").strip()), " ".join(a.text_content().split()))
                 for a in cells[4].iterfind(".//a[@href]")]  # fmt: skip
        out.append(Row(
            Registration(name=name, notes=notes, owners=_lines(cells[1]), date_registered=on,
                         date_registered_raw=raw_date, counties=counties,
                         county_geoids=county_geoids(counties, on)),
            files,
        ))  # fmt: skip
    return out
