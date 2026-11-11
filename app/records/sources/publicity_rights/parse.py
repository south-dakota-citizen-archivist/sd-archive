"""The registrations table → rows. Pure: no fetching, no database."""

from __future__ import annotations

from pathlib import Path

from records import fixes
from records.sources._tables import LayoutChanged, links, table, text
from sddocs.clean import parse_date

from .schema import Registration

COLUMNS = ["Name of Deceased Personality", "Name of Claimant", "Date Filed"]
FIXES = fixes.load(Path(__file__).with_name("fixes.toml"))
__all__ = ["COLUMNS", "FIXES", "LayoutChanged", "rows"]


def rows(html: str | bytes, base_url: str, corrections: list[fixes.Fix] = FIXES
         ) -> list[Registration]:  # fmt: skip
    """Every registration, in page order, with hand corrections applied. Raises LayoutChanged
    rather than skip a row it can't read."""
    out = []
    for n, (personality, claimant, filed) in table(html, COLUMNS):
        values = {"personality_name": text(personality), "claimant_name": text(claimant),
                  "date_filed_raw": text(filed)}  # fmt: skip
        if not values["personality_name"]:
            raise LayoutChanged(f"row {n} has no name")
        files = links(filed, base_url) or links(personality, base_url)
        values["form_url"] = files[0][0] if files else None
        values["corrections"] = fixes.apply(values, corrections)
        raw = values["date_filed_raw"]
        out.append(Registration(**values, date_filed=parse_date(raw) if raw else None))
    return out
