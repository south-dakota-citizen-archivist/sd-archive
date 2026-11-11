"""Felony offenders sentenced to the South Dakota Department of Corrections, from its offender
locator (docadultlookup.sd.gov): one record per person, keyed by DOC number, with the name,
demographics, current facility or parole office, correctional status and discharge date.
A person held in a facility is tied to the facility's city. The person is mentioned by the
listed name and by each alias, for matching across record sets.

The locator searches by last name prefix, so a run searches each letter, A to Z (in a Chrome
window: the search has an "I'm not a robot" box), and reads every page of each letter's
results (plain form posts). Then it reads each person's page (plain links), at most
`DETAILS_PER_RUN` new ones a run. Results and people's pages are kept by month: a stopped run
resumes, and the next month's run reads them again. A person whose page hasn't been read
this month keeps the last one read; until every letter has been searched and every person
has a page, the run is partial (nothing is marked dropped).
"""

from __future__ import annotations

import logging
import random
import string
from datetime import date
from pathlib import Path

from entities.mentions import from_text
from records.sources import Found, Item, Source, Stopped
from records.sources._places import city_place
from records.sources._sdcfr import form_fields

from . import parse

logger = logging.getLogger(__name__)
STATE = "0400000US46"
LETTERS = string.ascii_uppercase
DETAILS_PER_RUN = 20000


def browser_profile() -> Path:
    from django.conf import settings

    return Path(settings.MEDIA_ROOT) / "browser" / "doc-offenders"


def _searcher():
    from .search import Searcher

    return Searcher(browser_profile())


def letter_pages(fetcher, first: bytes) -> list[bytes]:
    """Every page of one search's results, from its first page."""
    pages, html, n = [first], first, 1
    while arg := parse.next_page(html, n):
        data = {k: v for k, v in form_fields(html).items() if k not in ("btnMission", "btnReset")}
        data |= {"__EVENTTARGET": parse.GRID, "__EVENTARGUMENT": arg}
        html = fetcher.request("POST", parse.URL, data=data, timeout=120).content
        n += 1
        pages.append(html)
    return pages


def stored_letter(store, month: str, letter: str) -> list[bytes] | None:
    if store is None or not store.has(f"results/{month}/{letter}/complete"):
        return None
    return [p.read_bytes() for p in sorted(store.root.glob(f"results/{month}/{letter}/page-*"))]


def latest_detail(store, month: str, doc: str) -> tuple[str | None, bytes | None]:
    if store is None:
        return None, None
    name = f"people/{month}/{doc}.html"
    if store.has(name):
        return month, store.read(name)
    earlier = sorted(store.root.glob(f"people/*/{doc}.html"), reverse=True)
    return (earlier[0].parent.name, earlier[0].read_bytes()) if earlier else (None, None)


def items(fetcher, today: date | None = None, details_per_run: int = DETAILS_PER_RUN) -> Found:
    month = f"{today or date.today():%Y-%m}"
    store = getattr(fetcher, "store", None)
    people: dict[str, dict] = {}
    counts: dict[str, int] = {}
    stopped = None
    searcher = None
    try:
        for letter in LETTERS:
            pages = stored_letter(store, month, letter)
            if pages is None:
                if searcher is None:
                    searcher = _searcher().__enter__()
                from .search import NeedsPerson

                try:
                    first = searcher.search(letter)
                except NeedsPerson as e:
                    stopped = str(e)
                    break
                pages = letter_pages(fetcher, first)
                if store is not None:
                    for i, page in enumerate(pages, start=1):
                        store.save(f"results/{month}/{letter}/page-{i:04d}.html", page,
                                   parse.URL, method="POST")  # fmt: skip
                    store.save(f"results/{month}/{letter}/complete", b"", allow_empty=True)
            rows = [r for page in pages for r in parse.results(page)]
            counts[letter] = len(rows)
            for row in rows:
                people.setdefault(row["doc_number"], row)
    finally:
        if searcher is not None:
            searcher.__exit__(None, None, None)

    todo = [d for d in people if latest_detail(store, month, d)[0] != month]
    random.shuffle(todo)
    fetched = 0
    for doc in todo:
        if fetched >= details_per_run:
            stopped = (
                stopped or f"read {fetched} people's pages this run; {len(todo) - fetched} left"
            )
            break
        html = fetcher.request("GET", parse.detail_url(people[doc]["detail_id"]),
                               timeout=120).content  # fmt: skip
        parse.detail(html)  # a page that isn't a person's stops the run here, not later
        if store is not None:
            store.save(
                f"people/{month}/{doc}.html", html, parse.detail_url(people[doc]["detail_id"])
            )
        fetched += 1

    out, missing = Found(), 0
    for doc, row in sorted(people.items(), key=lambda x: int(x[0])):
        page_month, html = latest_detail(store, month, doc)
        data, where = dict(row), [STATE]
        if html:
            data |= {**parse.detail(html), "page_month": page_month}
            if data.get("facility_state") == "SD" and (city := city_place(data["facility_city"])):
                where.append(city)  # where the person is held
        else:
            missing += 1
        out.append(Item(key=doc, data=data, source_url=parse.detail_url(row["detail_id"]),
                        places=where))  # fmt: skip
    out.checks = {"by_letter": counts, "people": len(out), "pages_read": fetched,
                  "without_page": missing,
                  "letters_searched": len(counts)}  # fmt: skip
    if stopped or missing or len(counts) < len(LETTERS):
        raise Stopped(out, stopped or f"{missing} people without a page read")
    return out


def mentions(record) -> list:
    """The person, by the name the locator lists and by each alias (family name first)."""
    d = record.data
    listed = f"{d['last_name']}, {d['first_name']} {d.get('middle_initial') or ''}".strip()
    names = [("name", listed), *(("aliases", a) for a in d.get("aliases") or [])]
    context = {"doc_number": d["doc_number"],
               "status": (d.get("fields") or {}).get("Correctional Status")}  # fmt: skip
    out, seen = [], set()
    for field, name in names:
        key = " ".join(name.replace(",", " ").replace(".", " ").upper().split())
        if key in seen:
            continue
        seen.add(key)
        out += from_text(name, dataset=record.recordset.dataset, record_key=str(record),
                         field=field, role="offender", order="family_first", start=len(out),
                         url=record.source_url, context=context)  # fmt: skip
    return out


SOURCE = Source(
    slug="doc-offenders", items=items, mentions=mentions,
    name="Department of Corrections offenders",
    dataset="doc-offenders", publisher="South Dakota Department of Corrections",
    base_url=parse.URL,
    description="Felony offenders sentenced to the Department of Corrections (prison), past "
                "and present: name, demographics, facility or parole office, status, discharge.",
)  # fmt: skip
