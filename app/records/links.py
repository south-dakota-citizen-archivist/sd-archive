"""Links to files in a web page: the simplest way a source finds its documents."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import urljoin, urlsplit

logger = logging.getLogger(__name__)

FILE_EXTENSIONS = frozenset({
    ".pdf", ".tif", ".tiff", ".jpg", ".jpeg", ".png", ".gif", ".webp",
    ".doc", ".docx", ".xls", ".xlsx", ".csv",
})  # fmt: skip


@dataclass(frozen=True)
class Link:
    url: str
    text: str = ""


def file_links(
    html: str | None,
    base_url: str = "",
    *,
    extensions: frozenset[str] | set[str] = FILE_EXTENSIONS,
    within: str = "",
) -> list[Link]:
    """Each distinct link to a file (by the URL path's extension) in page order, resolved
    against base_url, with its text. `within` is a CSS selector to look only inside (a
    results table, not the site's menus); if it matches nothing, the whole page is used."""
    if not html or not html.strip():
        return []
    import lxml.html

    root = lxml.html.fromstring(html)
    if within:
        found = root.cssselect(within)
        if found:
            root = found[0]
        else:
            logger.warning("file_links: %r matched nothing; using the whole page", within)
    wanted = {e.lower() for e in extensions}
    out, seen = [], set()
    for a in root.iter("a"):
        href = (a.get("href") or "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        if PurePosixPath(urlsplit(href).path).suffix.lower() not in wanted:
            continue
        url = urljoin(base_url, href) if base_url else href
        if url in seen:
            continue
        seen.add(url)
        out.append(Link(url, " ".join(a.text_content().split())))
    return out
