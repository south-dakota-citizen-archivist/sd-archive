"""Crawl a government's website within its own domain: every HTML page up to a page budget
and depth, breadth first, each page kept, its links and file links (PDFs, spreadsheets…)
noted but not downloaded. Links to other sites are kept too: a link to a portal on another
domain (a meetings system, a county map) is often how the site points to its records."""

from __future__ import annotations

import hashlib
import re
from collections import deque
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from urllib.parse import urldefrag, urljoin, urlsplit

FILE_TYPES = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv", ".zip", ".ppt", ".pptx",
              ".txt", ".rtf", ".mp3", ".mp4", ".jpg", ".jpeg", ".png", ".tif", ".tiff"}  # fmt: skip
SKIP_SCHEMES = ("mailto:", "tel:", "javascript:", "#", "data:")
# CivicPlus serves documents from paths like /DocumentCenter/View/123/Name
FILE_PATHS = re.compile(r"/(DocumentCenter/View|Archive\.aspx\?ADID|AgendaCenter/ViewFile)",
                        re.I)  # fmt: skip


@dataclass
class Link:
    url: str
    text: str


@dataclass
class Page:
    url: str
    depth: int
    title: str = ""
    headings: list[str] = field(default_factory=list)
    text: str = ""
    links: list[Link] = field(default_factory=list)  # pages on this site
    files: list[Link] = field(default_factory=list)  # documents, on any site
    external: list[Link] = field(default_factory=list)  # pages on other sites


def site_of(url: str) -> str:
    """The domain a crawl stays within: the host without www."""
    return (urlsplit(url).hostname or "").removeprefix("www.")


def on_site(url: str, site: str) -> bool:
    host = (urlsplit(url).hostname or "").removeprefix("www.")
    return host == site or host.endswith("." + site)


def is_file(url: str) -> bool:
    path = urlsplit(url).path
    return PurePosixPath(path).suffix.lower() in FILE_TYPES or bool(FILE_PATHS.search(url))


def read(html: bytes | str, url: str, depth: int, site: str) -> Page:
    import lxml.html

    root = lxml.html.fromstring(html if isinstance(html, bytes) else html.encode())
    for junk in root.xpath("//script|//style|//noscript|//svg"):
        junk.drop_tree()
    page = Page(url=url, depth=depth)
    title = root.find(".//title")
    page.title = " ".join((title.text_content() if title is not None else "").split())
    headings = [h for h in root.xpath("//h1|//h2|//h3") if h.text_content().strip()]
    page.headings = [" ".join(h.text_content().split()) for h in headings][:40]
    page.text = " ".join(root.text_content().split())
    seen = set()
    for a in root.iter("a"):
        href = (a.get("href") or "").strip()
        if not href or href.lower().startswith(SKIP_SCHEMES):
            continue
        target = urldefrag(urljoin(url, href))[0]
        if not target.startswith(("http://", "https://")) or target in seen:
            continue
        seen.add(target)
        link = Link(target, " ".join(a.text_content().split())[:200])
        if is_file(target):
            page.files.append(link)
        elif on_site(target, site):
            page.links.append(link)
        else:
            page.external.append(link)
    return page


def crawl(fetcher, start: str, *, max_pages: int = 300, max_depth: int = 4) -> list[Page]:
    """Pages of the site, breadth first from start. Pages are fetched through the fetcher's
    store when it has one (kept by URL), so a crawl run again reads what it has."""
    site = site_of(start)
    store = getattr(fetcher, "store", None)
    queue, queued, pages = deque([(start, 0)]), {start}, []
    while queue and len(pages) < max_pages:
        url, depth = queue.popleft()
        name = f"pages/{site}/{hashlib.sha1(url.encode()).hexdigest()}.html"
        try:
            if store is not None:
                html = fetcher.fetch(url, name)
            else:
                html = fetcher.request("GET", url, timeout=60).content
        except Exception:  # a broken link or a page that errors: noted by its absence
            continue
        if (
            not html
            or b"<html" not in html[:5000].lower()
            and b"<!doctype" not in html[:500].lower()
        ):
            continue
        page = read(html, url, depth, site)
        pages.append(page)
        if depth < max_depth:
            for link in page.links:
                if link.url not in queued:
                    queued.add(link.url)
                    queue.append((link.url, depth + 1))
    return pages
