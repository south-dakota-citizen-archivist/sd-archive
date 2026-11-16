"""Our readings of each bio page image, kept next to the image with the engine and its
positioned pieces: rapidocr for every page (bio-ocr/<image ID>.json), the second reading
after the legislature's own (the API's ParsedText); and Apple Vision for pages where those two
disagree (bio-ocr-vision/<image ID>.json), a third reading to break the tie.
"""

from __future__ import annotations

import json
import logging
from importlib.metadata import version

from .images import image_name

logger = logging.getLogger(__name__)

ENGINE = "rapidocr"
TIEBREAK = "vision"


def ocr_name(image_id: str, engine: str = ENGINE) -> str:
    folder = "bio-ocr" if engine == ENGINE else f"bio-ocr-{engine}"
    return f"{folder}/{image_id}.json"


def _version(engine: str) -> str:
    try:
        return version({"rapidocr": "rapidocr", "vision": "ocrmac"}.get(engine, engine))
    except Exception:
        return ""


def read_image(store, image_id: str, engine: str = ENGINE) -> dict:
    """The stored reading, or a new one."""
    name = ocr_name(image_id, engine)
    if store.has(name):
        return json.loads(store.read(name))
    from PIL import Image

    from sddocs import ocr

    with Image.open(store.path(f"bio-images/{image_id}.webp")) as image:
        pieces = ocr._to_points(ocr._engines[engine][0](image.convert("RGB")))
    reading = {"engine": engine, "engine_version": _version(engine), "pieces": pieces,
               "text": ocr.as_text(pieces, spacing=" ")}  # fmt: skip
    path = store.path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(reading))
    return reading


def read_all(store, engine: str = ENGINE, images: list[str] | None = None) -> dict:
    """Read every stored image (or the ones given) without a reading by the engine yet."""
    if images is None:
        images = sorted(p.stem for p in (store.root / "bio-images").glob("*.webp"))
    counts = {"read": 0, "already": 0}
    for n, image_id in enumerate(images, 1):
        if store.has(ocr_name(image_id, engine)):
            counts["already"] += 1
            continue
        read_image(store, image_id, engine)
        counts["read"] += 1
        if n % 250 == 0:
            logger.info("bio OCR: %d of %d", n, len(images))
    return counts


__all__ = ["image_name", "read_all", "read_image"]
