"""OCR: text from page images, as positioned pieces.

Engines (register more with register_engine):
- rapidocr: PaddleOCR's models on ONNX Runtime. CPU only, no system packages, runs anywhere
  including CI. The default. Needs the `ocr` extra.
- textra: Apple's Vision text recognition through the `textra` command (macOS).
- tesseract: the `tesseract` command.
- vision, livetext: Apple's Vision and LiveText frameworks through ocrmac (macOS; the
  `macos` extra).

Each returns pieces {"text", "conf" (0–1), "x0", "x1", "top", "bottom"} in PDF points from
the page's top left, so they line up with pdfplumber's words.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

DPI = 200
SCALE = DPI / 72  # pixels per PDF point

_engines: dict[str, tuple[Callable, Callable[[], bool]]] = {}
_rapid = None


def register_engine(name: str, run: Callable, available: Callable[[], bool] = lambda: True):
    """run(image) → pieces in pixels at SCALE (they're converted to points). available() says
    whether the engine can run here."""
    _engines[name] = (run, available)


def engines() -> list[str]:
    return list(_engines)


def available(engine: str) -> bool:
    return engine in _engines and _engines[engine][1]()


def render(pdf: str | Path, index: int, scale: float = SCALE):
    """One page (0-based) as a PIL image."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(pdf))
    try:
        return doc[index].render(scale=scale).to_pil()
    finally:
        doc.close()


def _to_points(pieces: list[dict]) -> list[dict]:
    keys = ("x0", "x1", "top", "bottom")
    return [{**p, **{k: p[k] / SCALE for k in keys}} for p in pieces]


def _rapidocr(image) -> list[dict]:
    global _rapid
    if _rapid is None:
        logging.getLogger("RapidOCR").setLevel(logging.ERROR)
        from rapidocr import RapidOCR

        _rapid = RapidOCR(params={"Global.log_level": "error"})
    result = _rapid(image)
    if not result.txts:
        return []
    out = []
    for text, score, box in zip(result.txts, result.scores, result.boxes, strict=False):
        xs, ys = [p[0] for p in box], [p[1] for p in box]
        out.append({"text": text, "conf": round(float(score), 3), "x0": min(xs), "x1": max(xs),
                    "top": min(ys), "bottom": max(ys)})  # fmt: skip
    return out


def _has_rapidocr() -> bool:
    try:
        import rapidocr  # noqa: F401
    except ImportError:
        return False
    return True


def _textra(image) -> list[dict]:
    with tempfile.TemporaryDirectory() as tmp:
        png, out = Path(tmp) / "page.png", Path(tmp) / "page.json"
        image.save(png)
        subprocess.run(["textra", "-s", str(png), "-p", str(out)], check=True, capture_output=True)
        data = json.loads(out.read_text())
    w, h = image.size
    pieces = []
    for obs in data.get("observations", data if isinstance(data, list) else []):
        box = obs.get("bounds") or obs.get("boundingBox") or {}
        # normalized coordinates, origin at the bottom left
        x, y = box.get("x", 0) * w, (1 - box.get("y", 0) - box.get("height", 0)) * h
        pieces.append({"text": obs.get("text", ""), "conf": obs.get("confidence"), "x0": x,
                       "x1": x + box.get("width", 0) * w, "top": y,
                       "bottom": y + box.get("height", 0) * h})  # fmt: skip
    return pieces


def _tesseract(image) -> list[dict]:
    """Lines from tesseract's TSV output (words joined per line)."""
    with tempfile.TemporaryDirectory() as tmp:
        png = Path(tmp) / "page.png"
        image.save(png)
        tsv = subprocess.run(["tesseract", str(png), "stdout", "tsv"], check=True,
                             capture_output=True, text=True).stdout  # fmt: skip
    lines: dict[tuple, list[dict]] = {}
    for row in csv.DictReader(io.StringIO(tsv), delimiter="\t"):
        if row.get("level") != "5" or not (row.get("text") or "").strip():
            continue
        key = (row["block_num"], row["par_num"], row["line_num"])
        lines.setdefault(key, []).append(row)
    out = []
    for words in lines.values():
        x0 = min(int(w["left"]) for w in words)
        top = min(int(w["top"]) for w in words)
        x1 = max(int(w["left"]) + int(w["width"]) for w in words)
        bottom = max(int(w["top"]) + int(w["height"]) for w in words)
        confs = [float(w["conf"]) for w in words if float(w["conf"]) >= 0]
        out.append({"text": " ".join(w["text"] for w in words),
                    "conf": round(sum(confs) / len(confs) / 100, 3) if confs else None,
                    "x0": x0, "x1": x1, "top": top, "bottom": bottom})  # fmt: skip
    return out


def _ocrmac(framework: str):
    def run(image) -> list[dict]:
        from ocrmac import ocrmac

        found = ocrmac.OCR(image, framework=framework, recognition_level="accurate",
                           language_preference=["en-US"]).recognize()  # fmt: skip
        w, h = image.size
        out = []
        for text, conf, (x, y, bw, bh) in found:
            # fractions of the image, origin at the bottom left
            box = {"x0": x * w, "x1": (x + bw) * w, "top": (1 - y - bh) * h, "bottom": (1 - y) * h}
            out.append({"text": text, "conf": round(float(conf), 3), **box})
        return out

    return run


def _has_ocrmac(framework: str) -> Callable[[], bool]:
    def check() -> bool:
        try:
            from ocrmac import ocrmac
        except ImportError:
            return False
        return framework == "vision" or bool(getattr(ocrmac, "LIVETEXT_AVAILABLE", False))

    return check


register_engine("rapidocr", _rapidocr, _has_rapidocr)
register_engine("textra", _textra, lambda: shutil.which("textra") is not None)
register_engine("tesseract", _tesseract, lambda: shutil.which("tesseract") is not None)
register_engine("vision", _ocrmac("vision"), _has_ocrmac("vision"))
register_engine("livetext", _ocrmac("livetext"), _has_ocrmac("livetext"))


def page_pieces(pdf: str | Path, index: int, engine: str = "rapidocr") -> list[dict]:
    """The text on one page (0-based), read by OCR, in PDF points."""
    if engine not in _engines:
        raise ValueError(f"unknown OCR engine {engine!r}; known: {engines()}")
    return _to_points(_engines[engine][0](render(pdf, index)))


def as_text(pieces: list[dict], tolerance: float = 5) -> str:
    """Positioned pieces as plain lines, top to bottom and left to right. Pieces close
    together (less than a character's height apart, as words are) are joined by a space; a
    wider gap, as between columns or a label and its answer, by two."""
    rows: list[list[dict]] = []
    for p in sorted(pieces, key=lambda p: ((p["top"] + p["bottom"]) / 2, p["x0"])):
        mid = (p["top"] + p["bottom"]) / 2
        if rows and abs((rows[-1][0]["top"] + rows[-1][0]["bottom"]) / 2 - mid) < tolerance:
            rows[-1].append(p)
        else:
            rows.append([p])
    lines = []
    for row in rows:
        row = sorted(row, key=lambda p: p["x0"])
        text = row[0]["text"]
        for prev, p in zip(row, row[1:], strict=False):
            gap = p["x0"] - prev["x1"]
            text += (" " if gap < (prev["bottom"] - prev["top"]) else "  ") + p["text"]
        lines.append(text)
    return "\n".join(lines)


def mean_confidence(pieces: list[dict]) -> float | None:
    scores = [p["conf"] for p in pieces if p.get("conf") is not None]
    return round(sum(scores) / len(scores), 3) if scores else None


def searchable(pdf: str | Path, out: str | Path, language: str = "eng") -> Path:
    """A copy of pdf with a text layer added to its scanned pages (ocrmypdf and tesseract;
    the `searchable` extra). Pages that already have text are left as they are."""
    import ocrmypdf

    ocrmypdf.ocr(str(pdf), str(out), language=language, skip_text=True, progress_bar=False)
    return Path(out)


def error_rate(got: str, reference: str) -> float:
    """Character error rate: edits to turn got into reference, per reference character,
    ignoring differences in spacing and line breaks."""
    from rapidfuzz.distance import Levenshtein

    a, b = " ".join(got.split()), " ".join(reference.split())
    return Levenshtein.distance(a, b) / max(len(b), 1)


def compare(
    pdf: str | Path,
    pages: list[int],
    engines: list[str] | None = None,
    reference: dict[int, str] | None = None,
) -> list[dict]:
    """Each engine on the same pages (1-based): seconds, characters, mean confidence and,
    against hand-checked reference text, the character error rate. For choosing an engine
    on evidence."""
    import time

    out = []
    for engine in engines or [e for e in engines_() if available(e)]:
        for n in pages:
            started = time.perf_counter()
            pieces = page_pieces(pdf, n - 1, engine)
            text = as_text(pieces)
            row = {"engine": engine, "page": n, "seconds": round(time.perf_counter() - started, 2),
                   "chars": len(text), "conf": mean_confidence(pieces)}  # fmt: skip
            if reference and n in reference:
                row["cer"] = round(error_rate(text, reference[n]), 4)
            out.append(row)
    return out


engines_ = engines  # compare() takes an `engines` argument
