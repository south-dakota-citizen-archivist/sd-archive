"""A second reading of each bio page image, by our own OCR (rapidocr), kept next to the
image at bio-ocr/<image ID>.json with the engine and its positioned pieces. The legislature's
own OCR (the API's ParsedText) is the first reading; parts are trusted where the two agree.
"""

from __future__ import annotations

import json
import logging
from importlib.metadata import version

from .images import image_name

logger = logging.getLogger(__name__)

ENGINE = "rapidocr"


def ocr_name(image_id: str) -> str:
    return f"bio-ocr/{image_id}.json"


def read_image(store, image_id: str) -> dict:
    """The stored reading, or a new one."""
    name = ocr_name(image_id)
    if store.has(name):
        return json.loads(store.read(name))
    from PIL import Image

    from sddocs import ocr

    with Image.open(store.path(f"bio-images/{image_id}.webp")) as image:
        pieces = ocr._to_points(ocr._engines[ENGINE][0](image.convert("RGB")))
    reading = {"engine": ENGINE, "engine_version": version("rapidocr"), "pieces": pieces,
               "text": ocr.as_text(pieces, spacing=" ")}  # fmt: skip
    path = store.path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(reading))
    return reading


def read_all(store) -> dict:
    """Read every stored image without a reading yet. Returns counts."""
    images = sorted(p.stem for p in (store.root / "bio-images").glob("*.webp"))
    counts = {"read": 0, "already": 0}
    for n, image_id in enumerate(images, 1):
        if store.has(ocr_name(image_id)):
            counts["already"] += 1
            continue
        read_image(store, image_id)
        counts["read"] += 1
        if n % 250 == 0:
            logger.info("bio OCR: %d of %d", n, len(images))
    return counts


__all__ = ["image_name", "read_all", "read_image"]
