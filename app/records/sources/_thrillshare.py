"""Thrillshare (Apptegy), the website platform many school districts use, read through the
documents API its sites use (thrillshare-cmsv2.services.thrillshare.com/api/v2/s/<site>/
documents): the site's Documents section as a tree of folders, each listing its files
(name, type, size, permanent URL) and subfolders, a page at a time."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit

API = "https://thrillshare-cmsv2.services.thrillshare.com/api/v2/s/{site}/documents"
_UUID = re.compile(r"/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/")
_MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
_DATE = re.compile(rf"\b({_MONTHS})\.?\s+(\d{{1,2}})\s*[,.\s]*\s(\d{{4}})\b", re.I)


FILES_HOST = "files-backend.assets.thrillshare.com"


def file_key(url: str) -> str | None:
    """A file's stable ID: the UUID in its path, or (older uploads) the token that is the
    whole path ("/./2isbvx5q012czfjjkc2tvro4v1k3"). None for a link to another site."""
    m = _UUID.search(url)
    if m:
        return m.group(1)
    parts = urlsplit(url)
    if parts.hostname == FILES_HOST:
        token = parts.path.rsplit("/", 1)[-1]
        return token or None
    return None


def is_file(url: str) -> bool:
    return urlsplit(url).hostname == FILES_HOST


def date_named(name: str) -> str | None:
    """The date a file's name gives ("November 9, 2026 School Board Minutes"), as ISO."""
    from datetime import datetime

    m = _DATE.search(name)
    if not m:
        return None
    try:
        named = f"{m.group(1).title()} {m.group(2)} {m.group(3)}"
        return datetime.strptime(named, "%B %d %Y").date().isoformat()
    except ValueError:
        return None


def _page(fetcher, site: str, folder: int | None, page: int) -> dict:
    params = {"page_no": page, **({"folder_id": folder} if folder else {})}
    return fetcher.request("GET", API.format(site=site), params=params, timeout=120).json()


def files(fetcher, site: str, folder: int | None = None, path: tuple[str, ...] = ()) -> list[dict]:
    """Every file under a folder (the whole Documents section by default), with its folder
    path, walking subfolders."""
    out, page, data = [], 1, None
    while True:
        data = _page(fetcher, site, folder, page)
        for item in data.get("items") or []:
            if item.get("url") and item.get("file_name"):
                out.append({"name": item["file_name"].strip(),
                            "extension": item.get("file_extension"),
                            "size": item.get("file_size"), "url": item["url"],
                            "short_url": item.get("short_url"), "folder_id": item.get("folder_id"),
                            "folder_path": list(path), "key": file_key(item["url"])})  # fmt: skip
        last = (data.get("meta") or {}).get("links", {}).get("last") or ""
        pages = int(parse_qs(urlsplit(last).query).get("page_no", ["1"])[0])
        if page >= pages:
            break
        page += 1
    for sub in (data.get("meta") or {}).get("folders") or []:
        out += files(fetcher, site, sub["id"], (*path, sub["folder_name"]))
    return out
