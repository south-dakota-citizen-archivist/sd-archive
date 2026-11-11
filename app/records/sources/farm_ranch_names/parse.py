"""The registrations table → rows. Pure: no fetching, no database."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import sdplaces
from records.sources._tables import LayoutChanged, lines, links, table, text
from sddocs.clean import parse_date

from .schema import Registration

COLUMNS = ["Farm, Ranch, Home Name", "Owner(s)", "Date Registered", "County", "Image"]
__all__ = ["COLUMNS", "LayoutChanged", "Row", "county_geoids", "parse_date", "rows"]


@dataclass
class Row:
    registration: Registration
    files: list[tuple[str, str]]  # (URL, link text): the certificate, and any amendment

    @property
    def certificate_url(self) -> str | None:
        return self.files[0][0] if self.files else None


def county_geoids(counties: list[str], on: date | None) -> list[str]:
    """Each county's GEOID, for the county in force on the date (Shannon before 2015)."""
    return [sdplaces.county(c, as_of=on).geoid for c in counties]


def rows(html: str | bytes, base_url: str) -> list[Row]:
    """Every registration in the table, in page order. Raises LayoutChanged rather than skip a
    row it can't read."""
    out = []
    for n, (name_cell, owners, registered, county, image) in table(html, COLUMNS):
        name, *notes = lines(name_cell) or [""]
        if not name:
            raise LayoutChanged(f"row {n} has no name")
        raw_date = text(registered)
        on = parse_date(raw_date) if raw_date else None
        counties = lines(county)
        out.append(Row(
            Registration(name=name, notes=notes, owners=lines(owners), date_registered=on,
                         date_registered_raw=raw_date, counties=counties,
                         county_geoids=county_geoids(counties, on)),
            links(image, base_url),
        ))  # fmt: skip
    return out
