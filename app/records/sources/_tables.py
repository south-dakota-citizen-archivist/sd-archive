"""Reading an HTML table the way published lists need: the expected columns or an error,
every row or an error, and cell text, lines and links as published."""

from __future__ import annotations

import re
from urllib.parse import quote, urljoin

import lxml.html


class LayoutChanged(ValueError):
    """The page isn't laid out the way the parser expects."""


def document(html: str | bytes):
    """Parse a page. Bytes are decoded as the page declares (UTF-8 when it doesn't say,
    Windows-1252 if that fails), never left for lxml to read as Latin-1."""
    if isinstance(html, bytes):
        declared = re.search(rb"""charset=["']?([\w-]+)""", html[:4096], re.I)
        try:
            html = html.decode(declared[1].decode() if declared else "utf-8")
        except (UnicodeDecodeError, LookupError):
            html = html.decode("cp1252", errors="replace")
    return lxml.html.fromstring(html)


def attr(el, name: str) -> str | None:
    """An attribute with its spacing normalized (non-breaking spaces too); None if empty."""
    return " ".join((el.get(name) or "").split()) or None


def text(cell) -> str:
    return " ".join(cell.text_content().split())


def lines(cell) -> list[str]:
    """The cell's text, one entry per line (names separated by <br>)."""
    return [" ".join(t.split()) for t in cell.itertext() if t.strip()]


def links(cell, base_url: str) -> list[tuple[str, str]]:
    """(absolute URL, link text) for each link in the cell. Spaces in an href are encoded."""
    return [
        (quote(urljoin(base_url, a.get("href").strip()), safe=":/?#[]@!$&'()*+,;=%~"), text(a))
        for a in cell.iterfind(".//a[@href]")
    ]


def _cells(tr) -> list:
    return [c for c in tr if c.tag in ("td", "th")]


def table(html: str | bytes, columns: list[str]) -> list[tuple[int, list]]:
    """The page's one table: (row number, cells) for each row after the header, which must be
    `columns`. Empty spacer rows are skipped; a row with the wrong number of cells raises."""
    tables = document(html).cssselect("table")
    if len(tables) != 1:
        raise LayoutChanged(f"expected one table, found {len(tables)}")
    trs = tables[0].cssselect("tr")
    if not trs or (header := [text(c) for c in _cells(trs[0])]) != columns:
        raise LayoutChanged(f"columns are {header if trs else []}, expected {columns}")
    out = []
    for n, tr in enumerate(trs[1:], start=2):
        cells = _cells(tr)
        if not any(text(c) for c in cells):
            continue
        if len(cells) != len(columns):
            raise LayoutChanged(f"row {n} has {len(cells)} cells, expected {len(columns)}")
        out.append((n, cells))
    return out
