"""Cleaning text and reading dates (the `text` extra: ftfy, dateparser)."""

from __future__ import annotations

from datetime import date, datetime


def fix_text(value: str) -> str:
    """Repair mojibake and stray encodings from OCR and scraped pages ('donâ€™t' → 'don't')."""
    import ftfy

    return ftfy.fix_text(value)


def parse_date(value: str, *, settings: dict | None = None) -> date | None:
    """A date in any of the formats filings use ('3/4/21', 'March 4, 2021', '2021-03-04'),
    read month first, as in the US. None if it isn't one."""
    import dateparser

    got = dateparser.parse(
        value.strip(),
        languages=["en"],
        settings={"DATE_ORDER": "MDY", "PREFER_DAY_OF_MONTH": "first", **(settings or {})},
    )
    return got.date() if isinstance(got, datetime) else None
