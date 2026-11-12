"""Read a Secretary of State "Registered Voters by County" report (PDF) into a snapshot: one
row per county with its registered voters by party, and the county's total active voters.

The report is a table over two pages: a line with the report's date, a header row naming the
parties (the order changes from report to report), a row per county, and a Total row. Columns
are read by their headings, never by position, and the report is checked before it's used:
66 counties, each party column adding up to the Total row, and each county's parties adding up
to its total active voters (inactive voters aren't active).
"""

from __future__ import annotations

import io
import re
from datetime import datetime

DATE = re.compile(r"\b(January|February|March|April|May|June|July|August|September|October|"
                  r"November|December)\s+(\d{1,2}),\s+(\d{4})\b", re.I)  # fmt: skip
# the report's column headings → the snapshot's columns
COLUMNS = {
    "county": "county", "democratic": "democratic", "republican": "republican",
    "libertarian": "libertarian", "constitution": "constitution", "independent": "independent",
    "oth": "other", "other": "other", "npa/ind": "npa_ind", "no party affiliation": "npa",
    "no labels": "no_labels", "inactive": "inactive", "total active": "total_active",
    "total": "total_active",
}  # fmt: skip
COUNTIES = 66


class ReportProblem(Exception):
    """The report can't be read as expected, or doesn't add up."""


def _heading(cell: str | None) -> str | None:
    if cell is None:
        return None
    text = " ".join(cell.split()).lower()
    if not text:
        return None
    if text not in COLUMNS:
        raise ReportProblem(f"unknown column heading {cell!r}")
    return COLUMNS[text]


def _number(cell: str | None) -> int:
    text = (cell or "").replace(",", "").strip()
    if not text:
        return 0
    if not text.isdigit():
        raise ReportProblem(f"not a count: {cell!r}")
    return int(text)


def report_date(text: str) -> str | None:
    m = DATE.search(text or "")
    if not m:
        return None
    return datetime.strptime(f"{m[1]} {m[2]} {m[3]}", "%B %d %Y").date().isoformat()


def _headings_from_text(text: str) -> list[str]:
    """The column headings in a header printed without rules ("County Democratic …
    No Party Inactive Total Active", with "Affiliation" on the next line): the longest known
    heading at each word."""
    words = " ".join(text.split()).lower().split()
    names = sorted(COLUMNS, key=lambda k: -len(k.split()))
    out, i = [], 0
    while i < len(words):
        for name in names:
            n = name.split()
            if words[i : i + len(n)] == n:
                out.append(COLUMNS[name])
                i += len(n)
                break
            if n[:2] == ["no", "party"] and words[i : i + 2] == ["no", "party"]:
                out.append(COLUMNS[name])  # "Affiliation" is on the next line
                i += 2
                break
        else:
            i += 1
    return out


def _tables(pdf) -> tuple[list[list], list[str] | None]:
    """The report's rows, and (for a table printed without rules) its column headings."""
    rows = []
    for page in pdf.pages:
        rows += page.extract_table() or []
    if any(any(_cell_is(c, "county") for c in r) for r in rows):
        return rows, None
    # no rules: columns from where the words line up, headings from the header's text
    rows, headings = [], None
    for page in pdf.pages:
        for line in (page.extract_text() or "").splitlines():
            if headings is None and line.lower().startswith("county "):
                headings = _headings_from_text(line)
        found = page.extract_table({"vertical_strategy": "text",
                                    "horizontal_strategy": "text"}) or []  # fmt: skip
        rows += [r for r in found if any(c for c in r)]
    return rows, headings


def read(content: bytes) -> tuple[str, list[str], list[dict]]:
    """The report's date, its party columns (in the report's order) and a row per county."""
    import pdfplumber

    with pdfplumber.open(io.BytesIO(content)) as pdf:
        text = pdf.pages[0].extract_text() or ""
        rows, headings = _tables(pdf)
    date = report_date(text)
    if date is None:
        raise ReportProblem("no report date on the first page")
    if headings is None:
        header_at = next((i for i, r in enumerate(rows)
                          if any(_cell_is(c, "county") for c in r)), None)  # fmt: skip
        if header_at is None:
            raise ReportProblem("no header row with a County column")
        columns = [_heading(c) for c in rows[header_at]]
        body = rows[header_at + 1 :]
    else:
        body = [
            r
            for r in rows
            if r[0]
            and DATE.search(r[0]) is None
            and r[0].strip()[:1].isupper()
            and not r[0].lower().startswith("county")
        ]
        width = max((len(r) for r in body), default=0)
        if len(headings) != width:
            raise ReportProblem(f"{len(headings)} headings ({headings}) for {width} columns")
        columns = headings
    if "county" not in columns or "total_active" not in columns:
        raise ReportProblem(f"no County or Total Active column: {columns}")
    parties = [c for c in columns if c not in (None, "county", "total_active")]
    out, total = [], None
    for r in body:
        cells = dict(zip(columns, r, strict=False))
        name = " ".join((cells.get("county") or "").split())
        if not name or _cell_is(name, "county") or DATE.search(name):
            continue  # the title and header, repeated on the second page
        values = {c: _number(cells.get(c)) for c in [*parties, "total_active"]}
        if name.lower().startswith("total"):
            total = values
            break
        out.append({"county": name, **values})
    check(out, parties, total)
    return date, parties, out


def _cell_is(cell, name: str) -> bool:
    return isinstance(cell, str) and " ".join(cell.split()).lower() == name


def check(rows: list[dict], parties: list[str], total: dict | None) -> None:
    if len(rows) != COUNTIES:
        raise ReportProblem(f"{len(rows)} counties, not {COUNTIES}")
    if total is None:
        raise ReportProblem("no Total row")
    for p in [*parties, "total_active"]:
        if sum(r[p] for r in rows) != total[p]:
            raise ReportProblem(f"{p}: the counties add up to {sum(r[p] for r in rows):,}, "
                                f"the Total row says {total[p]:,}")  # fmt: skip
    for r in rows:
        active = sum(r[p] for p in parties if p != "inactive")
        if active != r["total_active"]:
            raise ReportProblem(f"{r['county']}: parties add up to {active:,}, total active "
                                f"is {r['total_active']:,}")  # fmt: skip
