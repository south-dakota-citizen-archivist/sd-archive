"""Read the Secretary of State's polling place lists (PDF), one row per line of the list.

The lists' layouts, by the columns they print:

- 2016 general: County, Name, Address, City, State, Special Instructions, Vote Center. One
  row per polling place, with no precinct.
- 2018 primary and general: County, Precinct (Name), Polling Place, Address, City, Vote Center
  ("Vote Center" or, once, "Voter Center").
- 2020 to the 2026 primary and runoff: County, Precinct Name, Polling Place, Address, City,
  Instructions. Counties with vote centers list a precinct once per vote center.
- 2026 general: County, Name, Address, City, Special Instructions. One row per polling place.

Columns are found by their header, so a list with an unknown column stops (LayoutChanged)
rather than being read wrong.

Rows are read in the order the PDF draws its text, not by position alone. The lists are
spreadsheet exports with fixed row heights, so a cell's second line can be drawn inside the
next row ("Belle" / "Fourche" in the 2026 primary, where reading by position gives "Belle"
or "FBoeullerc he"), a vertically centered cell's first line sits above its row's county,
and a line can spill under a page's table (2026 primary, page 23: "Chambers"). The export
draws each row's cells one after another, starting with the county, so: a row starts where
text starts in the County column, and everything drawn until the next one belongs to it,
in the column its position says. Column edges come from the page's ruled table, or, where
the table finder can't see the rules (2026 runoff), from where the header's labels start.

Every character on a page is accounted for: in a row, the header, the list's title, an
"updated" date or a page number. Anything else is returned as unread, for the run's checks.

Absentee satellite site lists (2018, 2024) have their own columns (building, phone, days
open, …), kept as printed by header.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

HEADER_FIELDS = [  # most specific first
    ("precinct", re.compile(r"precin?c?t", re.I)),  # Precinct, Precinct Name, Precint Name
    ("polling_place", re.compile(r"polling place|^name$", re.I)),
    ("county", re.compile(r"^county$", re.I)),
    ("address", re.compile(r"^address", re.I)),
    ("city", re.compile(r"^city", re.I)),
    ("state", re.compile(r"^state$", re.I)),
    ("instructions", re.compile(r"instruction", re.I)),
    ("vote_center", re.compile(r"^vote center$", re.I)),
]
PAGE_NUMBER = re.compile(r"\bPAGE \d+ of \d+\b", re.I)
UPDATED = re.compile(r"(?:last )?updated:? ?([\d./-]+)", re.I)
SATELLITE_TOTAL = re.compile(r"^totals?$", re.I)
WORD_GAP = 12  # points between the words of one header label
SPILL = 15  # points under a cell's line that a continuation can sit
LEFT_OVERFLOW = 12  # points a centered cell's text can start left of its column
TOUCH = 1.0  # points between letters of one word (a new cell starts after its padding)


class LayoutChanged(ValueError):
    """The list doesn't look like any layout this reader knows."""


@dataclass
class Listing:
    rows: list[dict]
    title: str = ""
    updated: str | None = None  # "updated 10/25/2018", as printed
    unread: list[str] = field(default_factory=list)


def clean(cell) -> str:
    return " ".join(str(cell or "").split())


def is_header(text: str) -> bool:
    """The header line: County first, with Address and City (a wrapped "County Building"
    isn't one)."""
    words = text.lower().split()
    return bool(words) and words[0] == "county" and "address" in words and (
        "city" in words or "city/town" in words)  # fmt: skip


def fields_for(header: list[str]) -> list[str]:
    out = []
    for label in header:
        for name, pattern in HEADER_FIELDS:
            if pattern.search(label):
                out.append(name)
                break
        else:
            raise LayoutChanged(f"an unknown column: {label!r} in {header}")
    if out[0] != "county" or len(set(out)) != len(out):
        raise LayoutChanged(f"columns {header}")
    return out


def _lines(words: list[dict]) -> list[list[dict]]:
    """Words (or runs) grouped into lines by their top, left to right."""
    lines: list[list[dict]] = []
    for w in sorted(words, key=lambda w: (round(w["top"]), w["x0"])):
        if lines and abs(lines[-1][0]["top"] - w["top"]) < 2:
            lines[-1].append(w)
        else:
            lines.append([w])
    return [sorted(line, key=lambda w: w["x0"]) for line in lines]


def _text(line: list[dict]) -> str:
    return clean(" ".join(w["text"] for w in line))


def _labels(line: list[dict]) -> list[list[dict]]:
    """A header line's words grouped into labels: words close together make one."""
    labels: list[list[dict]] = []
    for w in line:
        if labels and w["x0"] - labels[-1][-1]["x1"] < WORD_GAP:
            labels[-1].append(w)
        else:
            labels.append([w])
    return labels


def _column(x: float, starts: list[float]) -> int:
    return max((i for i, s in enumerate(starts) if s <= x + 2), default=0)


def _runs(chars: list[dict], starts: list[float]) -> list[dict]:
    """Characters in drawing order joined into runs: one line of one cell. A run belongs to
    the column it starts in, and keeps going past the column's edge while its letters touch
    (a long word overflowing its cell: "Wessington Springs")."""
    runs: list[dict] = []
    for c in chars:
        last = runs[-1] if runs else None
        gap = c["x0"] - last["x1"] if last else None
        same_line = last is not None and abs(c["top"] - last["top"]) < 0.5
        touching = same_line and -1.5 <= gap < TOUCH
        spaced = same_line and gap < 3 and _column(c["x0"], starts) == last["col"]
        if touching or spaced:
            last["text"] += c["text"]
            last["x1"] = c["x1"]
        else:
            runs.append({"text": c["text"], "x0": c["x0"], "x1": c["x1"], "top": c["top"],
                         "bottom": c["bottom"], "col": _column(c["x0"], starts)})  # fmt: skip
    for run in runs:  # centered text wider than its cell starts a little left of it
        for i, edge in enumerate(starts):
            if edge - LEFT_OVERFLOW <= run["x0"] < edge and run["x1"] > edge + LEFT_OVERFLOW:
                run["col"] = i
    return [r for r in runs if r["text"].strip()]


class _Reader:
    def __init__(self):
        self.fields: list[str] | None = None
        self.starts: list[float] | None = None
        self.listing = Listing(rows=[])

    def note(self, text: str) -> None:
        """A line that isn't a row: the title, an update date, a page number."""
        footer = text.lower().startswith("south dakota secretary")
        if not text or footer or PAGE_NUMBER.search(text):
            return
        if not self.listing.title:
            self.listing.title = text
        if m := UPDATED.search(text):
            self.listing.updated = self.listing.updated or m[1].rstrip(".")
        elif text.strip("()").lower() != "subject to change" and text != self.listing.title:
            self.listing.unread.append(text)

    def page(self, page, number: int) -> None:
        words = page.extract_words()
        header = next((line for line in _lines(words) if is_header(_text(line))), None)
        tables = page.find_tables()
        widest = max(tables[0].rows, key=lambda r: sum(c is not None for c in r.cells),
                     default=None) if tables else None  # fmt: skip
        edges = [c[0] for c in widest.cells if c] if widest else []
        table = tables[0] if len(edges) > 1 else None
        if header:
            if table:  # the header's words split at the table's column edges
                labels = [[w for w in header if _column(w["x0"], edges) == i]
                          for i in range(len(edges))]  # fmt: skip
            else:
                labels = _labels(header)
            self.fields = fields_for([_text(label) for label in labels])
            self.starts = edges if table else [label[0]["x0"] for label in labels]
        elif table:
            self.starts = edges
        if self.fields is None:
            raise LayoutChanged(f"page {number}: rows before any header (County …)")
        if len(self.starts) != len(self.fields):
            raise LayoutChanged(f"page {number}: {len(self.starts)} columns under {self.fields}")
        x0, top, x1, bottom = table.bbox if table else (0, 0, page.width, page.height)
        if header:
            top = max(top, max(w["bottom"] for w in header))

        runs = _runs(page.chars, self.starts)
        body = [r for r in runs if r["top"] >= top - 1 and r["bottom"] <= bottom + SPILL]
        width = len(self.starts)
        rows, notes = (_by_drawing(runs, (x0, top, x1, bottom), width) if _row_wise(body)
                       else _by_position(runs, top, bottom, width))  # fmt: skip
        for row in rows:
            if row[-1] == "carried":
                row.pop()
                last = self.listing.rows[-1] if self.listing.rows else None
                if last is None:
                    notes += [r for pieces in row for r in pieces]
                    continue
                for name, pieces in zip(self.fields, row, strict=True):
                    if pieces:
                        last[name] = clean(f"{last[name]} {_cell_text(pieces)}")
                last["carried_to_page"] = number
                continue
            texts = [_cell_text(pieces) for pieces in row]
            if PAGE_NUMBER.search(" ".join(texts)) or not texts[0]:
                notes += [r for pieces in row for r in pieces]
                continue
            self.listing.rows.append({**dict(zip(self.fields, texts, strict=True)),
                                      "page": number})  # fmt: skip
        for line in _lines(notes):
            text = _note_text(line)
            if not is_header(text):
                self.note(text)


def _row_wise(runs: list[dict]) -> bool:
    """Whether the page draws its rows one after another: nothing drawn after a row's
    county sits higher than the row above it."""
    county_top, floor = None, None
    for run in runs:
        if run["col"] == 0:
            if county_top is not None and run["top"] > county_top + 1:
                floor = county_top
            county_top = run["top"]
        elif floor is not None and run["top"] < floor - 1:
            return False
    return True


def _by_drawing(runs: list[dict], box, width: int) -> tuple[list, list]:
    """Rows in drawing order: each starts at a run in the County column; the runs drawn
    until the next one are its cells' lines, wherever the export put them."""
    x0, top, x1, bottom = box
    rows, notes, previous = [], [], None
    for run in runs:
        inside = top - 1 <= run["top"] and run["bottom"] <= bottom + 1 and (
            x0 - 1 <= run["x0"] and run["x1"] <= x1 + 1)  # fmt: skip
        follows = (previous is not None and rows and previous["col"] == run["col"]
                   and 0 < run["top"] - previous["top"] <= SPILL)  # fmt: skip
        if run["top"] < top - 1 or not (inside or follows):
            notes.append(run)
            previous = None
            continue
        if run["col"] == 0 and (not rows or any(rows[-1][1:])):
            rows.append([[] for _ in range(width)])
        if not rows:
            notes.append(run)
            continue
        rows[-1][run["col"]].append(run)
        previous = run
    return rows, notes


def _by_position(runs: list[dict], top: float, bottom: float, width: int) -> tuple[list, list]:
    """Rows by position: a line with text in the County column starts a row, a line
    without continues the row above it."""
    rows, notes = [], []
    for line in _lines(runs):
        if line[0]["top"] < top - 1 or line[0]["top"] > bottom + 1:
            notes += line
            continue
        if any(r["col"] == 0 for r in line):
            rows.append([[] for _ in range(width)])
        elif not rows:  # above the page's first row: the last page's last row, wrapped
            rows.append([[] for _ in range(width)])
            rows[-1].append("carried")
        for run in line:
            rows[-1][run["col"]].append(run)
    return rows, notes


def _note_text(line: list[dict]) -> str:
    return _cell_text(line)


def _cell_text(pieces: list[dict]) -> str:
    """A cell's runs in drawing order: one line after another."""
    out = ""
    for i, run in enumerate(pieces):
        joined = i and abs(run["top"] - pieces[i - 1]["top"]) < 0.5 and (
            run["x0"] - pieces[i - 1]["x1"] < 1.5)  # fmt: skip
        out += run["text"] if joined else f" {run['text']}"
    return clean(out)


def read(content: bytes) -> Listing:
    """A polling place list's rows: each a dict of the columns it prints, plus its page."""
    import pdfplumber

    reader = _Reader()
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for number, page in enumerate(pdf.pages, 1):
            reader.page(page, number)
    return reader.listing


def satellite_sites(content: bytes) -> Listing:
    """An absentee satellite site list: each row's columns by their printed header."""
    import pdfplumber

    listing, header = Listing(rows=[]), None
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for number, page in enumerate(pdf.pages, 1):
            for raw in page.extract_table() or []:
                cells = [clean(c) for c in raw]
                if not any(cells):
                    continue
                if cells[0].lower() == "county":
                    header = cells
                    continue
                if header is None:
                    listing.title = listing.title or cells[0]
                    continue
                if not cells[0] and any(SATELLITE_TOTAL.match(c) for c in cells):
                    continue  # the totals line
                if not cells[0]:
                    raise LayoutChanged(f"page {number}: a row with no county: {cells}")
                printed = {h: c for h, c in zip(header, cells, strict=True) if h}
                listing.rows.append({"printed": printed, "page": number})
    if header is None:
        raise LayoutChanged("no header row (County …)")
    return listing
