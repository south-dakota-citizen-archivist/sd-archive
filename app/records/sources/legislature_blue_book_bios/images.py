"""The bio page images behind the Historical Listing's biographies, kept in the legislature's
raw store at bio-images/<image ID>.webp: the image's own number in its link
(biographicalimages/4989.webp). The API's DocumentId is the book, not the page.

The API links each image with a signature that expires in a day (an unsigned link answers
404), so a member's listing is fetched again just before its images are, and only for
members with an image not yet stored. Images are kept as served.
"""

from __future__ import annotations

import json
import logging

from records.sources._legislature.build import raw_root
from records.sources._legislature.crawl import API, store_name

logger = logging.getLogger(__name__)


def image_id(picture: dict) -> str:
    """The image's own number: biographicalimages/<n>.webp in its link."""
    return picture["Url"].split("?", 1)[0].rsplit("/", 1)[-1].removesuffix(".webp")


def image_name(picture: dict) -> str:
    return f"bio-images/{image_id(picture)}.webp"


def members_with_pictures() -> list[int]:
    folder = raw_root() / "api" / "Historical" / "Member"
    out = []
    for path in sorted(folder.glob("*.json")):
        if json.loads(path.read_text() or "{}").get("Pictures"):
            out.append(int(path.stem))
    return out


def fetch_images(fetcher, members: list[int] | None = None) -> dict:
    """Download every bio image not yet stored. Returns counts."""
    store = fetcher.store
    counts = {"members_refreshed": 0, "images": 0, "already_stored": 0, "failed": []}
    for mid in members if members is not None else members_with_pictures():
        path = f"Historical/Member/{mid}"
        stored = json.loads(store.read(store_name(path)) or b"{}")
        wanted = [p for p in stored.get("Pictures") or []
                  if not store.has(image_name(p))]  # fmt: skip
        counts["already_stored"] += len(stored.get("Pictures") or []) - len(wanted)
        if not wanted:
            continue
        fresh = json.loads(fetcher.fetch(API + path, store_name(path), refresh=True,
                                         headers={"Accept": "application/json"}))  # fmt: skip
        counts["members_refreshed"] += 1
        for picture in fresh.get("Pictures") or []:
            name = image_name(picture)
            if store.has(name):
                continue
            try:
                content = fetcher.fetch(picture["Url"], name)
            except Exception as e:  # noted; the next run tries again
                counts["failed"].append(f"{mid}/{image_id(picture)}: {type(e).__name__}")
                continue
            if not content.startswith(b"RIFF"):
                store.path(name).unlink(missing_ok=True)
                counts["failed"].append(f"{mid}/{image_id(picture)}: not an image")
                continue
            counts["images"] += 1
        if counts["members_refreshed"] % 200 == 0:
            logger.info("bio images: %s", {k: v if k != "failed" else len(v)
                                            for k, v in counts.items()})  # fmt: skip
    return counts
