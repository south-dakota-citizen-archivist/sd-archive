"""The registrations table → rows. Pure: no fetching, no database."""

from __future__ import annotations

from dataclasses import dataclass

from records.sources._tables import LayoutChanged, links, table, text
from sddocs.clean import parse_date

from .schema import Registration

COLUMNS = ["Athlete Agent Name", "Effective Date", "Expiration Date"]
__all__ = ["COLUMNS", "LayoutChanged", "Row", "rows"]


@dataclass
class Row:
    registration: Registration
    form_url: str | None


def rows(html: str | bytes, base_url: str) -> list[Row]:
    """Every registration in the table, in page order. Raises LayoutChanged rather than skip a
    row it can't read."""
    out = []
    for n, (name_cell, effective_cell, expiration_cell) in table(html, COLUMNS):
        name, effective, expiration = text(name_cell), text(effective_cell), text(expiration_cell)
        if not name:
            raise LayoutChanged(f"row {n} has no name")
        files = links(name_cell, base_url)
        out.append(Row(
            Registration(agent_name=name,
                         effective_date=parse_date(effective) if effective else None,
                         expiration_date=parse_date(expiration) if expiration else None,
                         effective_date_raw=effective, expiration_date_raw=expiration),
            files[0][0] if files else None,
        ))  # fmt: skip
    return out
