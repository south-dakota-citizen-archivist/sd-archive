"""Read a roster PDF's table by its header: each column starts where its header label does,
and each line's words go to the column they start in. Names with spaces stay whole ("Amato" |
"Anna Marie"), which splitting on whitespace can't do.

- A header can run over lines ("License" / "#", "Effective" / "Date"). Its lines are those
  made only of header words, around the first line with two or more of the columns' words.
- A word can head two columns ("Facility City", "Facility State"; "License Valid Through",
  "License Number"). Columns are given left to right, and the n-th time a word appears in the
  header, left to right, it heads the n-th column given for it. More appearances than columns
  given are labels' other words ("Original License Date"), and are ignored.
- A page without a header continues the table above it, at the same columns.
- A cell can wrap over lines above and below its row's line (a two-line "Funeral / Director"
  centered on the row). With an anchor column (the license number), a line without the anchor
  belongs to the nearest row that has one, within a line's height or so. A line too far from
  any row is a stray, returned apart so a run can report it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# words in headers that start no column ("Last Name", "License #", "Expiration Date")
FILLER = frozenset(["#", "Date", "Name", "Number", "No.", "-", "Home", "Issue", "Valid",
                    "Through", "Year", "Phone"])  # fmt: skip
WRAP = 12  # how far (points) a wrapped line can be from its row's line


class LayoutChanged(Exception):
    pass


@dataclass
class Table:
    rows: list[dict] = field(default_factory=list)
    strays: list[str] = field(default_factory=list)  # "page 3: text"


def _lines(words: list[dict], tolerance: float = 3) -> list[list[dict]]:
    rows: list[list[dict]] = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if rows and abs(rows[-1][0]["top"] - w["top"]) < tolerance:
            rows[-1].append(w)
        else:
            rows.append([w])
    return [sorted(r, key=lambda w: w["x0"]) for r in rows]


def _header(lines: list[list[dict]], spec: list[tuple[str, str]]) -> tuple[dict, int] | None:
    """Each column's left edge, and the index of the first line below the header."""
    words = {w for w, _ in spec}
    vocabulary = words | FILLER
    start = next((i for i, line in enumerate(lines)
                  if sum(w["text"] in words for w in line) >= 2), None)  # fmt: skip
    if start is None:
        return None

    def heading(line):  # "# Date Date" too: only header words
        return all(w["text"] in vocabulary for w in line)

    first = start
    while first > 0 and heading(lines[first - 1]) and \
            lines[first][0]["top"] - lines[first - 1][0]["top"] < 14:  # fmt: skip
        first -= 1
    end = start + 1
    while end < len(lines) and heading(lines[end]) and \
            lines[end][0]["top"] - lines[end - 1][0]["top"] < 14:  # fmt: skip
        end += 1
    seen: dict[str, int] = {}
    edges = {}
    for w in sorted((w for line in lines[first:end] for w in line), key=lambda w: w["x0"]):
        names = [name for word, name in spec if word == w["text"]]
        n = seen.get(w["text"], 0)
        seen[w["text"]] = n + 1
        if n < len(names):
            edges[names[n]] = w["x0"]
    return edges, end


def read(pdf, columns: dict[str, str] | list[tuple[str, str]], *, anchor: str | None = None,
         skip: re.Pattern | None = None) -> Table:  # fmt: skip
    """Every row of the table on every page, as {column: text, "_page": n}. columns: the
    header word each column starts with → its name, left to right ({"Last": "last_name", …}),
    or (word, name) pairs when a word heads more than one column. Lines above the header (a
    title, "as of …") and lines matching `skip` (a page footer) aren't rows."""
    spec = list(columns.items()) if isinstance(columns, dict) else list(columns)
    out, edges = Table(), None
    for page in pdf.pages:
        lines = _lines(page.extract_words(x_tolerance=1.5, keep_blank_chars=False))
        found = _header(lines, spec)
        if found is not None:
            edges, body = found
        elif edges is None:
            continue  # no table yet (a cover page)
        else:
            body = 0  # the table goes on, under the last page's header
        missing = {name for _, name in spec} - set(edges)
        if missing:
            raise LayoutChanged(f"page {page.page_number}: no header for {sorted(missing)}")
        order = sorted(edges.items(), key=lambda kv: kv[1])

        def column(w, order=order):
            col = order[0][0]
            for name, x in order:
                if w["x0"] >= x - 4:
                    col = name
            return col

        kept = [line for line in lines[body:]
                if not (skip and skip.search(" ".join(w["text"] for w in line)))]  # fmt: skip
        if anchor is None:
            groups = [[line] for line in kept]
        else:
            anchored = [i for i, line in enumerate(kept) if any(column(w) == anchor for w in line)]
            groups = {i: [kept[i]] for i in anchored}
            for i, line in enumerate(kept):
                if i in groups:
                    continue
                near = min(anchored, key=lambda j: abs(kept[j][0]["top"] - line[0]["top"]),
                           default=None)  # fmt: skip
                if near is None or abs(kept[near][0]["top"] - line[0]["top"]) > WRAP:
                    out.strays.append(f"page {page.page_number}: "
                                      + " ".join(w["text"] for w in line))  # fmt: skip
                    continue
                groups[near].append(line)
            groups = [groups[i] for i in anchored]
        for group in groups:
            cells: dict[str, list[str]] = {}
            for line in sorted(group, key=lambda line: line[0]["top"]):
                for w in line:
                    cells.setdefault(column(w), []).append(w["text"])
            out.rows.append({k: " ".join(v) for k, v in cells.items()}
                            | {"_page": page.page_number})  # fmt: skip
    return out
