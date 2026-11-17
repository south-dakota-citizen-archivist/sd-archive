"""Read the newer action reports, "Parole Board Actions for Public Release" (2021 on): one
line a case, in columns under the header, with each further county court the case lists on
a line of its own below.

DOC # | Last Name, First | Race | Sex | County | Hearing Type | Decision | Next Review

Reports from 2021 to 2023 have a text layer; later ones are scans, read by OCR (ocr.py), and
the same reader takes the engines' positioned pieces in place of words.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

COLUMNS = ["doc", "name", "race", "sex", "county", "hearing_type", "decision", "review"]
HEADER = {"DOC": "doc", "Last": "name", "Race": "race", "Sex": "sex", "County": "county",
          "Hearing": "hearing_type", "Decision": "decision", "Next": "review"}  # fmt: skip
# where the columns start on the page (the 2021–2023 text reports; scans within a few points)
USUAL = {"doc": 41, "name": 90, "race": 225, "sex": 266, "county": 297, "hearing_type": 464,
         "decision": 603, "review": 689}  # fmt: skip
FOOTER = re.compile(r"^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b|"
                    r"^Page \d+ of \d+", re.I)  # fmt: skip


class LayoutChanged(Exception):
    pass


# hearing types as these reports print them (a type can wrap: "Commutation-Paper Review" /
# "Hearing"); a line that starts one after a complete one starts another hearing
HEARING_TYPES = [
    "Parole Hearing", "PV Waiver Hearing", "SSV Waiver Hearing", "Early Discharge Hearing",
    "Earned Discharge Hearing", "Non-compliance Hearing", "Non-Compliance Waiver Hearing",
    "Commutation-Paper Review Hearing", "Commutation-Personal Appearance Hearing",
    "SSV Revocation Hearing", "PV Revocation Hearing", "Dispositional Meeting",
    "Suspended Sentence Dispositional Meeting", "Non-Compliance Dispositional",
    "Preliminary Hearing", "Case Review Hearing", "Appeal of Parole Date Hearing",
    "Compassionate Parole", "Remand Hearing", "Rescission Hearing",
]  # fmt: skip


@dataclass
class Case:
    page: int
    top: float = 0.0  # where its first line is on the page (scans' readings line up by it)
    cells: dict[str, str] = field(default_factory=dict)
    counties: list[str] = field(default_factory=list)
    hearings: list[dict] = field(default_factory=list)  # {hearing_type, decision, review}

    def add(self, col: str, text: str) -> None:
        if col == "hearing_type":
            last = self.hearings[-1] if self.hearings else None
            if last is None or last["hearing_type"] in HEARING_TYPES:
                self.hearings.append({"hearing_type": text, "decision": ""})
            else:
                last["hearing_type"] += " " + text
        elif col in ("decision", "review"):
            if not self.hearings:
                self.hearings.append({"hearing_type": "", "decision": ""})
            last = self.hearings[-1]
            last[col] = (last.get(col, "") + " " + text).strip()


def header(words: list[dict]) -> dict[str, float] | None:
    """Each column's left edge, from the header words (None: no header on the page)."""
    docs = [w for w in words if w["text"].strip().upper().startswith("DOC")]
    anchor = next((w for w in sorted(words, key=lambda w: w["top"])
                   if w["text"].strip().startswith("Hearing")
                   and any(abs(d["top"] - w["top"]) < 8 for d in docs)), None)  # fmt: skip
    if anchor is None:
        return None  # no header line ("DOC #" … "Hearing Type"): the summary page
    found = {}
    for w in words:
        first = w["text"].strip().split()[0] if w["text"].strip() else ""
        key = HEADER.get(first) or HEADER.get(first.upper()) or HEADER.get(first.title())
        if key and key not in found and abs(w["top"] - anchor["top"]) < 8:
            found[key] = w["x0"]
    missing = [c for c in COLUMNS if c not in found]
    if missing and len(found) >= len(COLUMNS) - 3:
        # a scan's header word or two not read: the report puts its columns in the same
        # places, so the rest come from the usual layout, moved as far as this page's are
        import statistics

        shift = statistics.median(found[c] - USUAL[c] for c in found)
        found |= {c: USUAL[c] + shift for c in missing}
    elif missing:
        raise LayoutChanged(f"header words missing: {sorted(missing)}")
    return {c: found[c] for c in COLUMNS}


def _lines(words: list[dict], tolerance: float = 4) -> list[list[dict]]:
    rows: list[list[dict]] = []
    for w in sorted(words, key=lambda w: ((w["top"] + w["bottom"]) / 2, w["x0"])):
        mid = (w["top"] + w["bottom"]) / 2
        if rows and abs((rows[-1][0]["top"] + rows[-1][0]["bottom"]) / 2 - mid) < tolerance:
            rows[-1].append(w)
        else:
            rows.append([w])
    return [sorted(r, key=lambda w: w["x0"]) for r in rows]


def _column(x0: float, edges: dict[str, float]) -> str:
    col = COLUMNS[0]
    for c in COLUMNS:
        if x0 >= edges[c] - 6:
            col = c
    return col


def cases(pages: list[tuple[int, list[dict]]]) -> list[Case]:
    """pages: (page number, positioned words or OCR pieces) for each page."""
    out: list[Case] = []
    last_edges = None
    for n, words in pages:
        try:
            edges = header(words)
        except LayoutChanged:
            if last_edges is None:
                raise
            edges = last_edges  # a scan's header not all read: the columns don't move
        if edges is None:
            continue  # the summary page
        last_edges = edges
        top = min(w["top"] for w in words if w["text"].strip().upper().startswith("DOC")) + 8
        for line in _lines([w for w in words if w["top"] > top]):
            text = " ".join(w["text"] for w in line)
            if FOOTER.search(text.strip()):
                continue
            cells: dict[str, list[str]] = {}
            for w in line:
                cells.setdefault(_column(w["x0"], edges), []).append(w["text"].strip())
            joined = {k: " ".join(v) for k, v in cells.items()}
            # a case starts at whatever is in the DOC # column: its number, or an OCR engine's
            # misreading of it ("SHESE" for 35345), which the other engine's reading shows up
            if joined.get("doc"):
                out.append(Case(page=n, top=min(w["top"] for w in line), cells={}))
            elif not out:
                continue
            case = out[-1]  # a case's cells wrap onto the lines below, column by column
            # the hearing type before the decision, so a new hearing takes its line's decision
            order = sorted(joined, key=lambda c: (c != "hearing_type", COLUMNS.index(c)))
            for col in order:
                text = joined[col]
                if col in ("hearing_type", "decision", "review"):
                    case.add(col, text)
                elif col == "county":
                    if case.counties and re.fullmatch(r"Court|County Court", text):
                        case.counties[-1] += " " + text  # "Charles Mix County" / "Court"
                    else:
                        case.counties.append(text)
                else:
                    case.cells[col] = (case.cells.get(col, "") + " " + text).strip()
    return out
