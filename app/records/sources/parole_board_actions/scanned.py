"""The scanned action reports (2024 on): the newer layout, read from three OCR engines'
pieces (ocr.py), case by case.

Three engines read each page (rapidocr, Apple Vision, and Tesseract word by word), and
their readings are lined up by where each case's first line is. A field at least two
engines read alike is kept; one they don't is left empty, every reading kept on the record
(`unconfirmed`, `readings`). Hearings are voted part by part: how many, then each one's
type, decision and review date (review dates with the engines' usual slips undone).

Vision reads the back of the sheet where it shows through, mirrored and at low confidence:
pieces under 0.5 are left out. A "case" only one engine finds, with no name or hearing read
(the footer seen through the paper), is set aside and listed on the run.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from . import ocr, offender, release

ENGINES = ("rapidocr", "textra", "tesseract_words")
FIELDS = ("doc", "name", "race", "sex", "county_courts", "hearings")
MIN_CONFIDENCE = 0.5


def _pages(pdf: Path, engine: str, key: str, n: int):
    out = []
    for i in range(n):
        found = ocr.pieces(pdf, i, engine, key, cached=True)
        if found is None:
            return None
        # the back of the sheet shows through some scans, and Vision reads it, mirrored
        # ("IHM" for WHI), at low confidence (0.3, where the page's own text is 1.0)
        out.append((i + 1, [{k: p[k] for k in ("text", "x0", "x1", "top", "bottom")}
                            for p in found if (p.get("conf") or 1) >= MIN_CONFIDENCE]))  # fmt: skip
    return out


def first_page_text(pdf: Path) -> str | None:
    """Page 1 as rapidocr read it (None: not read yet), to tell the layout."""
    from sddocs import ocr as engines

    found = ocr.pieces(pdf, 0, ENGINES[0], cached=True)
    return engines.as_text(found) if found is not None else None


def summary_text(pdf: Path, key: str, n: int) -> str:
    """The summary page as the engine that reads it whole: its outcomes add up to its "Total
    Inmates" (rapidocr can skip a line, "N/A 10"); failing that, the reading with the most
    outcomes read."""
    from sddocs import ocr as engines

    readings = []
    for engine in ENGINES:
        text = engines.as_text(ocr.pieces(pdf, n - 1, engine, key, cached=True) or [])
        s = offender.summary(text)
        whole = s["total_inmates"] is not None and sum(s["outcomes"].values()) == s["total_inmates"]
        readings.append((whole, sum(s["outcomes"].values()), text))
    return max(readings, key=lambda r: (r[0], r[1]))[2] if readings else ""


def _decision(text: str) -> str:
    known = {d.lower(): d for d in (*offender.DECISIONS, "Probable Cause",
                                    "Dismiss (No Violation)")}  # fmt: skip
    return known.get((text or "").lower(), text)


def _hearing_type(text: str) -> str:
    known = {t.lower(): t for t in release.HEARING_TYPES}
    return known.get((text or "").lower(), text)


MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
MISREAD_MONTHS = {"MAX": "MAY", "MAV": "MAY", "JUM": "JUN", "JUI": "JUL", "0CT": "OCT"}


def _review(text: str) -> str:
    """A next review date as printed ("01-MAY-25"), with the engines' usual slips undone:
    "O1-MAY-25" (a letter O), "01-MAX-25" (Vision's Y), "01-MAY -25" (a space). A reading
    that still isn't a date is kept as read."""
    m = re.fullmatch(r"([0-9O]{2})\s*-\s*([A-Z0-9]{3})\s*-\s*(\d{2})", (text or "").strip())
    if not m:
        return text
    day, month = m[1].replace("O", "0"), MISREAD_MONTHS.get(m[2], m[2])
    return f"{day}-{month}-{m[3]}" if month in MONTHS else text


def _value(case: release.Case, name: str):
    if name == "county_courts":
        return tuple(case.counties)
    if name == "hearings":  # the decision's case as the report prints it ("waived": Waived)
        return tuple((_hearing_type(h["hearing_type"]), _decision(h["decision"]),
                      _review(h.get("review", ""))) for h in case.hearings)  # fmt: skip
    return case.cells.get(name)


def _hearings(values: dict[str, tuple | None]) -> tuple[tuple | None, list[str]]:
    """The hearings, voted part by part: as many hearings as two engines read, then each
    one's type, decision and review date where two engines read it alike (a part read
    differently is left empty and named in what's returned: "hearing 2 review"; a part no
    engine reads was printed blank). None when no two
    engines read the same number of hearings."""
    counts = Counter(len(v) for v in values.values() if v)
    agreed = [n for n, k in counts.items() if k >= 2]
    if len(agreed) != 1:
        return None, ["hearings"]
    out, unsettled = [], []
    for i in range(agreed[0]):
        parts = []
        for j, part in enumerate(("type", "decision", "review")):
            read = Counter(v[i][j] for v in values.values() if v and len(v) > i and v[i][j])
            two = [x for x, k in read.items() if k >= 2]
            if len(two) == 1:
                parts.append(two[0])
            elif not read:
                parts.append(None)  # no engine read one: printed blank (a second hearing's date)
            else:
                parts.append(None)
                unsettled.append(f"hearing {i + 1} {part}")
        out.append(tuple(parts))
    return tuple(out), unsettled


def read(pdf: Path) -> tuple[list[dict], dict] | None:
    """The report's cases as {page, top, row, fields, unconfirmed, readings}, and what was
    found (None: not every page has been read by every engine yet)."""
    import pdfplumber

    key = ocr.digest(pdf)
    with pdfplumber.open(pdf) as doc:
        n = len(doc.pages)
    by_engine = {}
    for engine in ENGINES:
        pages = _pages(pdf, engine, key, n)
        if pages is None:
            return None
        by_engine[engine] = release.cases(pages)
    # line the readings up: a case is the same case where it starts on the same page, at
    # the same height (within 6 points)
    slots: list[dict[str, release.Case]] = []
    for engine in ENGINES:
        for case in by_engine[engine]:

            def same(slot, case=case):
                return any(c.page == case.page and abs(c.top - case.top) < 6
                           for c in slot.values())  # fmt: skip

            slot = next((s for s in slots if engine not in s and same(s)), None)
            if slot is None:
                slots.append({engine: case})
            else:
                slot[engine] = case
    out, noise = [], []
    for slot in slots:
        if len(slot) == 1 and not _found(next(iter(slot.values()))):
            # one engine's "case" with no name and no hearing: the page's footer seen through
            # the paper, mirrored ("Wa OF:I FOZ SI Asnony repsInuI"), or specks
            only = next(iter(slot.values()))
            noise.append({"page": only.page, "engine": next(iter(slot)),
                          "text": only.cells.get("doc")})  # fmt: skip
            continue
        out.append(_combine(slot))
    out.sort(key=lambda c: (c["page"], c["top"]))
    row, last_page = 0, None
    for c in out:
        row = row + 1 if c["page"] == last_page else 1
        last_page = c["page"]
        c["row"] = row
    return out, {"pages": n, "summary": summary_text(pdf, key, n), "noise": noise}


def _found(case: release.Case) -> bool:
    """Whether a reading looks like a case: a name or a hearing read."""
    return bool(case.cells.get("name") or case.hearings)


def _combine(slot: dict[str, release.Case]) -> dict:
    """A case's fields: each one at least two engines read alike; the rest unconfirmed,
    every engine's reading kept."""
    base = next(iter(slot.values()))
    fields, unconfirmed, readings = {}, [], {}
    for name in FIELDS:
        values = {e: _value(slot[e], name) if e in slot else None for e in ENGINES}
        if name == "hearings":
            fields[name], unsettled = _hearings(values)
            unconfirmed += unsettled
            if unsettled or len({v for v in values.values()}) > 1:
                readings[name] = values  # what each engine read, where any differs
            continue
        counted = Counter(v for v in values.values() if v not in (None, "", ()))
        agreed = [v for v, k in counted.items() if k >= 2]
        if len(agreed) == 1:
            fields[name] = agreed[0]
            if counted[agreed[0]] < len(slot):
                readings[name] = values  # settled two to one: what the third read, kept
        else:
            fields[name] = None
            unconfirmed.append(name)
            readings[name] = values
    return {"page": base.page, "top": min(c.top for c in slot.values()), "fields": fields,
            "unconfirmed": unconfirmed, "readings": readings,
            "engines": sorted(slot)}  # fmt: skip
