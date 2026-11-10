"""Tables from PDF pages with text layers (pdfplumber)."""

from __future__ import annotations

LINES = {"vertical_strategy": "lines", "horizontal_strategy": "lines"}
TEXT = {"vertical_strategy": "text", "horizontal_strategy": "text"}


def clean_cell(value) -> str:
    return " ".join(str(value or "").split())


def tables(page, settings: dict | None = None) -> list[list[list[str]]]:
    """Every table on a pdfplumber page as rows of cleaned cells. settings: pdfplumber's
    table settings; LINES (ruled tables, the default) or TEXT (tables laid out by spacing)."""
    found = page.extract_tables(settings or LINES)
    return [[[clean_cell(c) for c in row] for row in t] for t in found]


def rows_above(page, top: float, tolerance: float = 3) -> list[list[str]]:
    """Text lines above y=top (a header printed outside a table's ruling), each split on
    wide gaps."""
    words = [w for w in page.extract_words() if w["bottom"] <= top + tolerance]
    rows: list[list[dict]] = []
    for w in sorted(words, key=lambda w: (round(w["top"]), w["x0"])):
        if rows and abs(rows[-1][0]["top"] - w["top"]) < tolerance:
            rows[-1].append(w)
        else:
            rows.append([w])
    out = []
    for row in rows:
        cells, current, last_x1 = [], [], None
        for w in row:
            if last_x1 is not None and w["x0"] - last_x1 > 10:
                cells.append(" ".join(current))
                current = []
            current.append(w["text"])
            last_x1 = w["x1"]
        cells.append(" ".join(current))
        out.append(cells)
    return out
