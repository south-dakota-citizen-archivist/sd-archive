"""Felony offenders sentenced to the South Dakota Department of Corrections, from its offender
locator (docadultlookup.sd.gov): one record per person, keyed by DOC number, with the name,
demographics, current facility or parole office, correctional status and discharge date.
A person held in a facility is tied to the facility's city. The person is mentioned by the
listed name and by each alias, for matching across record sets.

The locator searches by last name prefix, so a run searches each letter, A to Z (in a Chrome
window: the search has an "I'm not a robot" box), and reads every page of each letter's
results (plain form posts). It goes at a person's pace: searches 40 to 80 minutes apart,
results pages 5 to 12 seconds apart, and people's pages (plain links) 8 to 20 seconds apart,
with a break of 3 to 10 minutes every 30 to 70 pages; people's pages are read between
searches and after them, at most `DETAILS_PER_RUN` new ones a run. Results and
people's pages are kept by month: a stopped run resumes, and the next month's run reads them
again. A person whose page hasn't been read
this month keeps the last one read; until every letter has been searched and every person
has a page, the run is partial (nothing is marked dropped).
"""

from __future__ import annotations

import logging
import random
import string
import time
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
DETAILS_PER_RUN = 5000
# the pace, in seconds: a person's, not a program's
SEARCH_GAP = (40 * 60.0, 80 * 60.0)  # between searches (each has the "I'm not a robot" box)
PAGE_GAP = (5.0, 12.0)  # between a search's results pages
DETAIL_GAP = (8.0, 20.0)  # between people's pages
BREAK_EVERY = (30, 70)  # people's pages between breaks
BREAK = (3 * 60.0, 10 * 60.0)  # a break


def browser_profile() -> Path:
    from django.conf import settings

    return Path(settings.MEDIA_ROOT) / "browser" / "doc-offenders"


def _searcher():
    from .search import Searcher

    return Searcher(browser_profile())


def letter_pages(fetcher, first: bytes, sleep=time.sleep) -> list[bytes]:
    """Every page of one search's results, from its first page."""
    pages, html, n = [first], first, 1
    while arg := parse.next_page(html, n):
        sleep(random.uniform(*PAGE_GAP))
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


class _People:
    """The people found so far, and reading their pages up to the run's budget."""

    def __init__(self, fetcher, store, month: str, budget: int, sleep=time.sleep):
        self.fetcher, self.store, self.month, self.budget = fetcher, store, month, budget
        self.sleep = sleep
        self.found: dict[str, dict] = {}
        self.todo: list[str] = []
        self.read = 0
        self.until_break = random.randint(*BREAK_EVERY)

    def add(self, rows: list[dict]) -> None:
        new = [r for r in rows if r["doc_number"] not in self.found]
        for row in new:
            self.found[row["doc_number"]] = row
        month = self.month
        todo = [r["doc_number"] for r in new
                if latest_detail(self.store, month, r["doc_number"])[0] != month]  # fmt: skip
        random.shuffle(todo)
        self.todo += todo

    def read_one(self) -> bool:
        """Read one person's page; False if there's none to read or the budget is spent."""
        if not self.todo or self.read >= self.budget:
            return False
        if self.read:
            self.sleep(random.uniform(*DETAIL_GAP))
            self.until_break -= 1
            if self.until_break <= 0:
                self.sleep(random.uniform(*BREAK))
                self.until_break = random.randint(*BREAK_EVERY)
        doc = self.todo.pop()
        url = parse.detail_url(self.found[doc]["detail_id"])
        html = self.fetcher.request("GET", url, timeout=120).content
        parse.detail(html)  # a page that isn't a person's stops the run here, not later
        if self.store is not None:
            self.store.save(f"people/{self.month}/{doc}.html", html, url)
        self.read += 1
        return True


def items(fetcher, today: date | None = None, details_per_run: int | None = None,
          search_gap: tuple[float, float] | None = None, clock=time.monotonic,
          sleep=time.sleep) -> Found:  # fmt: skip
    """Searches are at least search_gap seconds apart; people's pages are read in between
    (and after), up to details_per_run."""
    details_per_run = DETAILS_PER_RUN if details_per_run is None else details_per_run
    search_gap = SEARCH_GAP if search_gap is None else search_gap
    month = f"{today or date.today():%Y-%m}"
    store = getattr(fetcher, "store", None)
    people = _People(fetcher, store, month, details_per_run, sleep)
    counts: dict[str, int] = {}
    stopped, searcher, next_search = None, None, clock()
    try:
        for letter in LETTERS:
            pages = stored_letter(store, month, letter)
            if pages is None:
                from .search import NeedsPerson

                while clock() < next_search:  # read pages until the next search is due
                    if not people.read_one():
                        sleep(max(0.0, next_search - clock()))
                if searcher is None:
                    searcher = _searcher().__enter__()
                try:
                    first = searcher.search(letter)
                except NeedsPerson as e:
                    stopped = str(e)
                    break
                next_search = clock() + random.uniform(*search_gap)
                pages = letter_pages(fetcher, first, sleep)
                if store is not None:
                    for i, page in enumerate(pages, start=1):
                        store.save(f"results/{month}/{letter}/page-{i:04d}.html", page,
                                   parse.URL, method="POST")  # fmt: skip
                    store.save(f"results/{month}/{letter}/complete", b"", allow_empty=True)
            rows = [r for page in pages for r in parse.results(page)]
            counts[letter] = len(rows)
            people.add(rows)
    finally:
        if searcher is not None:
            searcher.__exit__(None, None, None)
    while people.read_one():
        pass
    if people.todo and not stopped:
        stopped = f"read {people.read} people's pages this run; {len(people.todo)} left"
    fetched = people.read
    people = people.found

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
