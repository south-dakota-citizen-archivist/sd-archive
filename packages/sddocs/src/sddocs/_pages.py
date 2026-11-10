"""Reading PDFs page by page, each page the best way it allows, with a cache per page."""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Iterable, Iterator
from pathlib import Path

from . import forms, ocr

MIN_CHARS = 30  # fewer characters than this in the text layer: treat the page as scanned


def sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def _quiet() -> None:
    logging.getLogger("pdfminer").setLevel(logging.ERROR)


def kind(pdf: str | Path) -> str:
    """What a PDF is, without OCR: "form" (has fillable fields), "native" (every page has a
    text layer), "scanned" (none does), "mixed", or "empty" (no pages)."""
    import pdfplumber

    _quiet()
    with pdfplumber.open(str(pdf)) as doc:
        if not doc.pages:
            return "empty"
        if any(forms.has_fields(p) for p in doc.pages):
            return "form"
        texty = [len(p.chars) > MIN_CHARS for p in doc.pages]
    if all(texty):
        return "native"
    return "scanned" if not any(texty) else "mixed"


class PageCache:
    """One JSON file per page read: <root>/<sha256[:2]>/<sha256>/<n>-<key>.json. The key holds
    the reader version and OCR engine, so changing either reads the page again."""

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def path(self, digest: str, n: int, key: str) -> Path:
        return self.root / digest[:2] / digest / f"{n}-{key}.json"

    def get(self, digest: str, n: int, key: str) -> dict | None:
        p = self.path(digest, n, key)
        return json.loads(p.read_text()) if p.exists() else None

    def put(self, digest: str, n: int, key: str, page: dict) -> None:
        p = self.path(digest, n, key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".partial")
        tmp.write_text(json.dumps(page, ensure_ascii=False))
        tmp.replace(p)


def _words(page) -> list[dict]:
    return [{"text": w["text"], "x0": w["x0"], "x1": w["x1"], "top": w["top"],
             "bottom": w["bottom"]} for w in forms.upright_words(page)]  # fmt: skip


def pages(
    pdf: str | Path,
    *,
    ocr_engine: str | None = "rapidocr",
    only: Iterable[int] | None = None,
    cache: str | Path | PageCache | None = None,
    version: int | str = 1,
    min_chars: int = MIN_CHARS,
) -> Iterator[dict]:
    """Each page (or the 1-based page numbers in `only`) read the best way it allows:

    - "fields": it has fillable form fields. `fields` holds them (forms.widgets), and
      `text` and `words` come from its text layer.
    - "text": it has a text layer.
    - "ocr": it doesn't, so it's read by OCR (ocr_engine, if available). `conf` is the
      engine's mean confidence; `words` are its positioned pieces.
    - "empty": no text layer and no OCR.

    Each page is {"n", "method", "text", "words", "fields", "conf", "engine"}. With a cache,
    a page read before (same file, reader version and engine) comes from the cache, so an
    interrupted run picks up where it stopped. Bump `version` when what you keep changes.
    """
    import pdfplumber

    _quiet()
    store = PageCache(cache) if isinstance(cache, (str, Path)) else cache
    digest = sha256(pdf) if store else ""
    use_ocr = bool(ocr_engine) and ocr.available(ocr_engine)
    key = f"v{version}-{ocr_engine if use_ocr else 'noocr'}"
    wanted = set(only) if only is not None else None
    with pdfplumber.open(str(pdf)) as doc:
        for i, page in enumerate(doc.pages):
            n = i + 1
            if wanted is not None and n not in wanted:
                continue
            if store and (hit := store.get(digest, n, key)) is not None:
                yield hit
                continue
            out: dict = {"n": n, "fields": [], "conf": None, "engine": None}
            has_text = len(page.chars) > min_chars
            if forms.has_fields(page):
                out |= {"method": "fields", "fields": forms.widgets(page),
                        "text": page.extract_text() or "", "words": _words(page)}  # fmt: skip
            elif has_text:
                out |= {"method": "text", "text": page.extract_text() or "", "words": _words(page)}
            elif use_ocr:
                pieces = ocr.page_pieces(pdf, i, ocr_engine)
                out |= {"method": "ocr", "text": ocr.as_text(pieces), "words": pieces,
                        "conf": ocr.mean_confidence(pieces), "engine": ocr_engine}  # fmt: skip
            else:
                out |= {"method": "empty", "text": "", "words": []}
            if store:
                store.put(digest, n, key, out)
            yield out


def text(pdf: str | Path, **kwargs) -> str:
    """All of a PDF's text, pages separated by form feeds."""
    return "\f".join(p["text"] for p in pages(pdf, **kwargs))
