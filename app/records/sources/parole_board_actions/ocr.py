"""OCR for the scanned action reports (2024 on): each page read by every engine sddocs has
(rapidocr; Apple Vision through textra and Tesseract where installed), the pieces kept by the
report's contents and page, so reading again is never needed.

    python manage.py shell -c "from records.sources.parole_board_actions import ocr; ocr.read_all()"
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)
ENGINES = ("rapidocr", "textra", "tesseract_words")


def raw_root() -> Path:
    from django.conf import settings

    return Path(settings.MEDIA_ROOT) / "raw" / "parole-board-actions"


def digest(pdf: Path) -> str:
    return hashlib.sha256(pdf.read_bytes()).hexdigest()[:16]


def pieces(pdf: Path, index: int, engine: str, key: str | None = None,
           cached: bool = False) -> list[dict] | None:  # fmt: skip
    """One page's OCR pieces, read once and kept. None if the engine isn't available, or
    (cached=True: a run reads only what read_all has read) the page hasn't been read."""
    from sddocs import ocr

    path = raw_root() / "ocr" / (key or digest(pdf)) / f"{index:03d}.{engine}.json"
    if path.exists():
        return json.loads(path.read_text())
    if cached or not ocr.available(engine):
        return None
    found = ocr.page_pieces(pdf, index, engine)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(found))
    return found


def is_scan(pdf: Path) -> bool:
    import pdfplumber

    with pdfplumber.open(pdf) as doc:
        return all(len(p.chars) < 30 for p in doc.pages[:3])


def read_all(engines=ENGINES) -> dict:
    """Read every stored scanned report's pages that haven't been read yet."""
    import pdfplumber

    done = {"reports": 0, "pages": 0}
    for pdf in sorted((raw_root() / "reports").glob("*")):
        if not is_scan(pdf):
            continue
        key = digest(pdf)
        with pdfplumber.open(pdf) as doc:
            n = len(doc.pages)
        for i in range(n):
            for engine in engines:
                pieces(pdf, i, engine, key)
            done["pages"] += 1
        done["reports"] += 1
        logger.info("%s: %d pages read", pdf.name, n)
    return done
