"""The scanned action reports (2024 on): the newer layout, read from three OCR engines'
pieces (ocr.py), case by case.

Three engines read each page (rapidocr, Apple Vision, and Tesseract word by word), and
their readings are lined up by where each case's first line is. A field at least two
engines read alike is kept; one they don't is left empty, every reading kept on the record
(`unconfirmed`, `readings`). Vision reads the back of the sheet where it shows through,
mirrored and at low confidence: pieces under 0.5 are left out.
"""

from __future__ import annotations

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
    from sddocs import ocr as engines

    found = ocr.pieces(pdf, n - 1, ENGINES[0], key, cached=True) or []
    return engines.as_text(found)


def _decision(text: str) -> str:
    known = {d.lower(): d for d in (*offender.DECISIONS, "Probable Cause",
                                    "Dismiss (No Violation)")}  # fmt: skip
    return known.get((text or "").lower(), text)


def _hearing_type(text: str) -> str:
    known = {t.lower(): t for t in release.HEARING_TYPES}
    return known.get((text or "").lower(), text)


def _value(case: release.Case, name: str):
    if name == "county_courts":
        return tuple(case.counties)
    if name == "hearings":  # the decision's case as the report prints it ("waived": Waived)
        return tuple((_hearing_type(h["hearing_type"]), _decision(h["decision"]),
                      h.get("review", "")) for h in case.hearings)  # fmt: skip
    return case.cells.get(name)


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
    out = [_combine(slot) for slot in slots]
    out.sort(key=lambda c: (c["page"], c["top"]))
    row, last_page = 0, None
    for c in out:
        row = row + 1 if c["page"] == last_page else 1
        last_page = c["page"]
        c["row"] = row
    return out, {"pages": n, "summary": summary_text(pdf, key, n)}


def _combine(slot: dict[str, release.Case]) -> dict:
    """A case's fields: each one at least two engines read alike; the rest unconfirmed,
    every engine's reading kept."""
    base = next(iter(slot.values()))
    fields, unconfirmed, readings = {}, [], {}
    for name in FIELDS:
        values = {e: _value(slot[e], name) if e in slot else None for e in ENGINES}
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
