"""The Secretary of State's document searches (sdsos.gov `…/search/Default.aspx`): raffle
requests, oaths of office, proclamations, executive orders, newspaper circulation statements,
local bond statements, financial interest statements and the 2000–2016 campaign finance
filings all use one system.

- The search is an ASP.NET form: the page is fetched, its form posted back with the search
  fields (most searches accept a blank search, which lists everything), and the results come
  back as one table in `#lbl_SearchInfo`. Plain HTTP; no browser.
- Each row links to the document's viewer (`Document.aspx?CabId=…&DocGuid=…`): a page of
  scanned page images, not a file. `assemble()` fetches the images in order and makes one PDF
  (or merges the PDF pages some filings are stored as). The DocGuid is the document's key.
  The images are served only to a session that has opened the search page and then the
  viewer. A batch is one session, as a person browsing filings would be: the search page is
  opened once, then each document's viewer and its page images. With `SOS_BROWSER` set
  (headless or headed), documents are read through a Chrome window (`_sos_browser`).
- The server sometimes answers with a "we're sorry" page instead of results or a page image.
  A page image that gets one is asked for again after a wait (`IMAGE_RETRIES`); if it still
  does, that document fails (`PageUnavailable`: some documents' images are like that) and the
  batch goes on, unless the document before failed the same way, which makes it a block. A
  "we're sorry" search page or viewer, or the server's error page, is a block at once
  (`Blocked`).

**The system goes down easily, so it's used slowly**: one request at a time, each waiting
`PAUSE` after the one before, and the first block stops the run (and a document batch)
rather than trying the next thing.

`search_source()` makes a record set's source from a search: its URL, its columns and the
names it lists.
"""

from __future__ import annotations

import atexit
import io
import os
import random
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from urllib.parse import parse_qs, urljoin, urlsplit

from records.sources import DocumentLink, Found, Item, Source

# seconds between any two requests to the search system (at 10 or 30 it sends us to its error
# page after about 20 requests; a minute has held)
PAUSE = (60.0, 60.0)
IMAGE_RETRIES = (60.0,)  # seconds to wait before asking for a page image again
_sleep = time.sleep
_last = [0.0]  # when the last request went out (time.monotonic)
# Documents through a Chrome window (_sos_browser): "headless", "headed", or "" for plain HTTP
BROWSER = os.environ.get("SOS_BROWSER", "")
_window: list = []

BLOCK_PHRASES = ("we're sorry", "we are sorry", "temporarily unavailable", "access denied",
                 "too many requests", "please try again later")  # fmt: skip
STATE = "0400000US46"


class Blocked(Exception):
    """The server answered with a block or error page instead of the content."""


class LayoutChanged(Exception):
    pass


class PageUnavailable(Exception):
    """A document's page image kept answering "we're sorry": that document fails, and the
    batch goes on."""


SORRY_IN_A_ROW = 2  # documents whose page images keep answering "we're sorry", then a block
_sorry = [0]  # documents in a row whose page images did


def _pace(sleep=time.sleep, clock=time.monotonic) -> None:
    """Wait until PAUSE has passed since the last request to the system."""
    wait = random.uniform(*PAUSE) - (clock() - _last[0])
    if _last[0] and wait > 0:
        sleep(wait)
    _last[0] = clock()


def check_block(text: str, url: str) -> None:
    lowered = text.lower()
    if "error.aspx" in url.lower():
        raise Blocked(f"{url}: the search sent us to its error page")
    for phrase in BLOCK_PHRASES:
        if phrase in lowered and "lbl_searchinfo" not in lowered:
            raise Blocked(f"{url}: the server answered {phrase!r}")


def is_viewer(url: str) -> bool:
    """A document viewer page on sdsos.gov (not sdcfr.sdsos.gov, whose Document.aspx serves
    the file itself)."""
    host = urlsplit(url).netloc.lower()
    return (
        host.endswith("sdsos.gov")
        and not host.startswith("sdcfr.")
        and ("document.aspx" in url.lower() or "getimage.aspx" in url.lower())
    )


def doc_guid(url: str) -> str | None:
    return parse_qs(urlsplit(url).query).get("DocGuid", [None])[0]


def cab_id(url: str) -> str | None:
    return parse_qs(urlsplit(url).query).get("CabId", [None])[0]


# --- the search ------------------------------------------------------------------------


def search(fetcher, url: str, fields: dict[str, str] | None = None) -> bytes:
    """The search's results page: the form posted back with fields (by input name)."""
    import lxml.html

    _pace()
    page = fetcher.request("GET", url, timeout=120)
    check_block(page.text, page.url)
    forms = lxml.html.fromstring(page.content).forms
    if not forms:
        raise LayoutChanged(f"{url}: no search form")
    form = forms[0]
    data = {el.get("name"): el.get("value") or "" for el in form.inputs
            if el.get("name") and el.get("type") not in ("submit", "button")}  # fmt: skip
    for name, value in (fields or {}).items():
        if name not in data:
            raise LayoutChanged(f"{url}: no field {name!r} in the form ({sorted(data)})")
        data[name] = value
    data["cmdSubmit"] = "Search"
    _pace()
    results = fetcher.request("POST", page.url, data=data, timeout=300,
                              headers={"Referer": page.url})  # fmt: skip
    check_block(results.text, results.url)
    return results.content


def rows(html: bytes, base: str) -> tuple[list[str], list[dict]]:
    """The results table's headings, and each row: its cells by heading (spaces closed up)
    and its document link."""
    import lxml.html

    found = lxml.html.fromstring(html).xpath('//*[@id="lbl_SearchInfo"]')
    if not found:
        raise LayoutChanged("no results (#lbl_SearchInfo)")
    trs = found[0].xpath(".//tr")
    if not trs:
        return [], []  # "No matches found."
    head = [" ".join(c.text_content().split()) for c in trs[0].xpath("./th|./td")]
    out = []
    for tr in trs[1:]:
        cells = [" ".join(c.text_content().split()) for c in tr.xpath("./td")]
        hrefs = [a.get("href") for a in tr.xpath(".//a[@href]")]
        if not cells:
            continue
        out.append({"cells": dict(zip(head, cells, strict=False)),
                    "url": urljoin(base, hrefs[0]) if hrefs else None})  # fmt: skip
    return head, out


DATE_FORMATS = ("%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y", "%Y%m%d", "%Y-%m-%d")


def parse_date(text: str | None) -> str | None:
    """An ISO date from a listed one ("9/25/2015 12:00:00 AM", "9/25/2015", "20150925"), or
    None when it isn't a whole date (some are 6 or 7 digits, or words)."""
    text = (text or "").strip()
    for fmt in DATE_FORMATS:
        try:
            d = datetime.strptime(text, fmt).date()
        except ValueError:
            continue
        if fmt == "%Y%m%d" and len(text) != 8:
            continue
        return d.isoformat() if date(1850, 1, 1) <= d <= date(2100, 1, 1) else None
    return None


# --- the document ----------------------------------------------------------------------


def _open_search(fetcher, search_url: str) -> None:
    """The search page, once for the fetcher's session (each later document reuses it)."""
    opened = getattr(fetcher, "_sos_opened", None)
    if opened is None:
        opened = set()
        fetcher._sos_opened = opened
    if search_url in opened:
        return
    _pace()
    check_block(fetcher.request("GET", search_url, timeout=120).text, search_url)
    opened.add(search_url)


def _image(fetcher, url: str, viewer_url: str):
    """A page image, asked for again after a wait when the answer is a "we're sorry" page."""
    for wait in (*IMAGE_RETRIES, None):
        _pace()
        resp = fetcher.request("GET", url, timeout=120, headers={"Referer": viewer_url})
        body = resp.content
        if not body[:200].lstrip().lower().startswith((b"<!doctype", b"<html")):
            return resp
        if "error.aspx" in str(getattr(resp, "url", url)).lower():
            check_block(resp.text, url)
        if wait is None:
            try:
                check_block(resp.text, url)
            except Blocked as e:
                _sorry[0] += 1
                if _sorry[0] >= SORRY_IN_A_ROW:
                    raise Blocked(f"{e} ({_sorry[0]} documents in a row)") from None
                raise PageUnavailable(f"{e}, after {len(IMAGE_RETRIES) + 1} tries") from None
            raise LayoutChanged(f"{url}: a web page, not a page image")
        try:
            check_block(resp.text, url)
        except Blocked:
            _sleep(wait)
            continue
        raise LayoutChanged(f"{url}: a web page, not a page image")
    raise AssertionError("unreachable")


def browser_fetcher():
    """The batch's Chrome window (one for the process, closed when it exits)."""
    if not _window:
        from django.conf import settings

        from records.sources._sos_browser import BrowserFetcher

        window = BrowserFetcher(settings.MEDIA_ROOT / "browser" / "sos",
                                headless=BROWSER != "headed")  # fmt: skip
        atexit.register(window.close)
        _window.append(window)
    return _window[0]


def assemble(fetcher, viewer_url: str) -> bytes:
    """A filing's scanned pages, from its viewer, as one PDF (each request paced): the
    search page (once a session), the viewer, then each page image in order. With BROWSER
    set, through a Chrome window instead of the fetcher."""
    import lxml.html
    from PIL import Image

    if BROWSER:
        fetcher = browser_fetcher()
    search_url = urljoin(viewer_url, "Default.aspx")
    _open_search(fetcher, search_url)
    _pace()
    page = fetcher.request("GET", viewer_url, timeout=120, headers={"Referer": search_url})
    if page.content[:5] == b"%PDF-":
        return page.content  # a link that serves the filing itself
    check_block(page.text, page.url)
    if "document thumbnails" not in page.text.lower():
        raise LayoutChanged(f"{viewer_url}: not a document viewer page")
    root = lxml.html.fromstring(page.content)
    links = [urljoin(page.url, a.get("href")) for a in root.xpath("//table//a[@href]")]

    def number(url):
        n = parse_qs(urlsplit(url).query).get("Image", ["0"])[0]
        return int(n) if n.isdigit() else 0

    links = sorted(dict.fromkeys(links), key=number)
    if not links:
        raise LayoutChanged(f"{viewer_url}: no page images")
    images, pdfs = [], []
    for url in links:
        body = _image(fetcher, url, viewer_url).content
        if body[:5] == b"%PDF-":
            pdfs.append(body)
            continue
        images.append(Image.open(io.BytesIO(body)).convert("RGB"))
    _sorry[0] = 0  # every page came: the next document starts the count again
    if pdfs and images:
        raise LayoutChanged(f"{viewer_url}: both PDF and image pages")
    if pdfs:
        if len(pdfs) == 1:
            return pdfs[0]
        import pikepdf

        merged = pikepdf.Pdf.new()
        for body in pdfs:
            with pikepdf.open(io.BytesIO(body)) as src:
                merged.pages.extend(src.pages)
        buf = io.BytesIO()
        merged.save(buf)
        return buf.getvalue()
    buf = io.BytesIO()
    images[0].save(buf, format="PDF", resolution=200.0, save_all=True,
                   append_images=images[1:])  # fmt: skip
    return buf.getvalue()


# --- a record set's source -------------------------------------------------------------


@dataclass
class Column:
    heading: str  # as the results table heads it
    field: str  # the record's field
    kind: str = "text"  # text, date, year


@dataclass
class Named:
    """A party a row names, for matching: the fields that make up the name, joined."""

    fields: tuple[str, ...]
    role: str
    order: str = "given_first"  # entities.mentions order for people; "auto" for organizations


@dataclass
class SearchSpec:
    slug: str
    url: str
    columns: list[Column]
    named: list[Named] = field(default_factory=list)
    searches: list[dict] = field(default_factory=lambda: [{}])  # field values per search
    name: str = ""
    description: str = ""


def _value(kind: str, text: str):
    text = text.strip()
    if not text:
        return None
    if kind == "date":
        return parse_date(text)
    if kind == "year":
        return int(text) if text.isdigit() else None
    return text


def row_data(spec: SearchSpec, row: dict) -> dict:
    """A record's data: the DocGuid and cabinet, each column's value (dates as ISO, years as
    numbers) and, under `listed`, every cell as the search listed it."""
    data = {"doc_guid": doc_guid(row["url"]), "cab_id": cab_id(row["url"])}
    for c in spec.columns:
        # some rows leave out their trailing empty cells
        data[c.field] = _value(c.kind, row["cells"].get(c.heading, ""))
    data["listed"] = row["cells"]
    return data


def items_for(spec: SearchSpec):
    def items(fetcher) -> Found:
        out, seen, counts = Found(), set(), {}
        for fields in spec.searches:
            html = search(fetcher, spec.url, fields)
            store = getattr(fetcher, "store", None)
            if store is not None:
                tag = "-".join(f"{k}={v}" for k, v in fields.items()) or "all"
                store.save(f"searches/{tag}.html", html, spec.url, method="POST")
            head, found = rows(html, spec.url)
            missing = [c.heading for c in spec.columns if found and c.heading not in head]
            if missing:
                raise LayoutChanged(f"{spec.slug}: no column {missing} (the table has {head})")
            counts["-".join(fields.values()) or "all"] = len(found)
            for row in found:
                if not row["url"] or not doc_guid(row["url"]):
                    raise LayoutChanged(f"{spec.slug}: a row without a document: {row['cells']}")
                data = row_data(spec, row)
                key = data["doc_guid"]
                if key in seen:
                    continue
                seen.add(key)
                # the record links to its filing (the primary source), not the search page
                out.append(Item(key=key, data=data, source_url=row["url"],
                                documents=[DocumentLink(row["url"], f"{key}.pdf")],
                                places=[STATE]))  # fmt: skip
        out.checks = {"listed": counts, "documents": len(out)}
        return out

    return items


def mentions_for(spec: SearchSpec):
    from entities.mentions import from_text

    def mentions(record) -> list:
        d, out = record.data, []
        for n in spec.named:
            name = " ".join(str(d.get(f)).strip() for f in n.fields if d.get(f))
            if name:
                out += from_text(name, dataset=record.recordset.dataset, record_key=str(record),
                                 field="+".join(n.fields), role=n.role, order=n.order,
                                 url=record.source_url, start=len(out))  # fmt: skip
        return out

    return mentions


def legacy_key(record) -> str:
    """An imported record's key (the viewer URL) as this source's: the DocGuid."""
    return doc_guid(record.key) or record.key


def from_legacy_for(spec: SearchSpec):
    """An imported record's data in this source's shape: the same fields, dates as ISO (the
    earlier archive kept some as listed and some reformatted)."""

    def from_legacy(data: dict) -> dict:
        out = {}
        for c in spec.columns:
            raw = data.get(c.field)
            out[c.field] = _value(c.kind, str(raw)) if raw is not None else None
        return out

    return from_legacy


def search_source(spec: SearchSpec) -> Source:
    return Source(slug=spec.slug, items=items_for(spec), mentions=mentions_for(spec),
                  name=spec.name, dataset=spec.slug, publisher="South Dakota Secretary of State",
                  base_url=spec.url, description=spec.description,
                  from_legacy=from_legacy_for(spec), legacy_key=legacy_key)  # fmt: skip
