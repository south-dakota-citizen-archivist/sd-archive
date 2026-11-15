"""Read the statistical reports' scanned pages (the 2019–2021 district tables, 18 pages of
2018's) with OCR, so report.py can read them like the pages that have text.

Each page without a text layer is rendered at 300 dpi and read with rapidocr (PaddleOCR
models on ONNX Runtime). Its way up is found by a quick read of each of the four turns
(the municipality tables are landscape on portrait pages; a few recap pages are upside down):
the turn that reads the most text in boxes wider than tall. Every text box is saved with its
position and confidence (.cache/property-tax/ocr/<year>/<page>.json), so the tables can be re-read
without running OCR again; `page_text` rebuilds the page's lines from them, top to bottom,
each line's boxes left to right.

    uv run python pipelines/property-tax/ocr.py [--years 2018,2019,2020,2021]
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
RAW = REPO / ".cache" / "property-tax" / "raw" / "statistical-reports"
OCR = REPO / ".cache" / "property-tax" / "ocr"
DPI = 300
READER = 2  # bump when how pages are read changes; pages read by an older one are read again


def _engine():
    from rapidocr import RapidOCR

    return RapidOCR()


def read_page(engine, doc, index: int) -> dict:
    """One page's text boxes: [x0, y0, x1, y1, text, confidence], top-left origin."""
    import numpy as np

    def run(rotation: int, dpi: int = DPI) -> list[list]:
        image = doc[index].render(scale=dpi / 72, rotation=rotation).to_pil()
        # no per-box flipping: a box read upside down ("896$" for "$698") is a misread, and a
        # page upside down should read badly so its turn is found
        result = engine(np.array(image), use_cls=False)
        boxes = []
        found = result.boxes if result.boxes is not None else []
        for box, text, score in zip(found, result.txts or [], result.scores or [], strict=True):
            xs, ys = [p[0] for p in box], [p[1] for p in box]
            boxes.append([round(min(xs)), round(min(ys)), round(max(xs)), round(max(ys)),
                          text, round(float(score), 4)])  # fmt: skip
        return boxes

    def quality(boxes: list[list]) -> float:
        return statistics.mean(b[5] for b in boxes) if boxes else 0.0

    def legible(boxes: list[list]) -> float:
        """Characters read, weighted by confidence, in boxes wider than tall (rapidocr turns
        a tall box to read it, so a sideways page reads nearly as well otherwise)."""
        return sum(len(b[4]) * b[5] for b in boxes if (b[2] - b[0]) > (b[3] - b[1]))

    # which way up: a quick low-resolution read of each turn, then one full read
    rotation = max((0, 90, 180, 270), key=lambda r: legible(run(r, dpi=100)))
    boxes = run(rotation)
    return {"rotation": rotation, "dpi": DPI, "reader": READER, "boxes": boxes}


def page_text(page: dict) -> str:
    """The page's lines: boxes grouped by their vertical middles (within half a line), each
    line left to right. A two-column table (townships: a county's townships run down each
    half of the page) is read a column at a time, left then right, as the text-layer
    reports read."""
    boxes = page["boxes"]
    head = " ".join(b[4] for b in sorted(boxes, key=lambda b: b[1])[:3]).upper()
    if "TOWNSHIP VALUATION" in head:
        # the left half ends just after its "TAXES" heading (right-aligned amounts under it;
        # the right half's names start well clear of it); the title is above the headings
        headings = sorted((b for b in boxes if b[4].strip().upper() in ("TAXES", "TAXABLE VALUE")),
                          key=lambda b: b[0])  # fmt: skip
        taxes = [b for b in headings if b[4].strip().upper() == "TAXES"]
        if len(taxes) == 2:
            middle = taxes[0][2] + (taxes[0][3] - taxes[0][1]) * 2.5
            top = min(b[1] for b in headings)
        else:
            middle = (min(b[0] for b in boxes) + max(b[2] for b in boxes)) / 2
            top = min((b[1] for b in boxes if not (b[0] < middle < b[2])), default=0)
        title = [b for b in boxes if b[3] <= top]
        rest = [b for b in boxes if b[3] > top]
        left = [b for b in rest if (b[0] + b[2]) / 2 < middle]
        right = [b for b in rest if (b[0] + b[2]) / 2 >= middle]
        return "\n".join(_lines(part) for part in (title, left, right) if part)
    return _lines(boxes)


def _clean(text: str) -> str:
    """A letter O read in a number ("$o", "1,2O4") is a zero; a star ("★", the scan's
    asterisk) is the asterisk; an S before a number ("S16,239") is a dollar sign; and a
    dollar amount in groups of three digits ("$54.693,169") is whole dollars, its periods
    misread commas (amounts with cents end in two digits)."""
    text = text.replace("★", "*")
    text = re.sub(r"(?<![A-Za-z])[Ss](?=\d)", "$", text)
    text = re.sub(r"(?<=\$)[oO](?![A-Za-z])", "0", text)
    text = re.sub(r"(?<=\d)[oO](?=[\d,.])|(?<=[\d,])[oO](?=\d)", "0", text)
    return re.sub(r"\$\d{1,3}(?:[.,]\d{3})+(?![\d.,])",
                  lambda m: m[0].replace(".", ","), text)  # fmt: skip


def _lines(boxes: list[list]) -> str:
    boxes = sorted(boxes, key=lambda b: (b[1] + b[3]) / 2)
    if not boxes:
        return ""
    height = statistics.median(b[3] - b[1] for b in boxes)
    lines, current, middle = [], [], None
    for b in boxes:
        m = (b[1] + b[3]) / 2
        if middle is not None and m - middle > height * 0.5:
            lines.append(current)
            current = []
        current.append(b)
        middle = statistics.mean((x[1] + x[3]) / 2 for x in current)
    lines.append(current)
    return "\n".join(_clean(" ".join(b[4] for b in sorted(line, key=lambda b: b[0])))
                     for line in lines)  # fmt: skip


def stored(year: int, page: int) -> dict | None:
    path = OCR / str(year) / f"{page:03d}.json"
    if not path.exists():
        return None
    found = json.loads(path.read_text())
    return found if found.get("reader") == READER else None


def pages_without_text(content: bytes) -> list[int]:
    import report

    return report.no_text(report.pages(content), "")


def main() -> None:
    import pypdfium2 as pdfium

    ap = argparse.ArgumentParser()
    ap.add_argument("--years", default="2018,2019,2020,2021")
    args = ap.parse_args()
    engine = None
    for year in (int(y) for y in args.years.split(",")):
        content = (RAW / f"{year}.pdf").read_bytes()
        todo = [n for n in pages_without_text(content) if stored(year, n) is None]
        print(f"{year}: {len(todo)} pages to read")
        doc = pdfium.PdfDocument(content)
        try:
            for n in todo:
                if stored(year, n) is not None:  # read meanwhile by another process
                    continue
                engine = engine or _engine()
                page = read_page(engine, doc, n - 1)
                path = OCR / str(year) / f"{n:03d}.json"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(page))
                score = statistics.mean(b[5] for b in page["boxes"]) if page["boxes"] else 0
                print(f"  {year} p{n}: {len(page['boxes'])} boxes, rotation {page['rotation']}, "
                      f"confidence {score:.3f}", flush=True)  # fmt: skip
        finally:
            doc.close()


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(HERE))
    main()
