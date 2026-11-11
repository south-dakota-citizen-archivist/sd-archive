"""Private lobbyist registrations (one per lobbyist, employer and year), from the Secretary
of State's lobbyist search.

The printer-friendly PDF lists every registration; its names give the surnames to search
(each search returns every year); each result's detail page is read and is the record,
keyed by its CN. The registration's filings (authorizations, expense reports) are its
documents. The run is checked against the PDF: names and years found vs. listed.

Detail pages for the last two years are fetched again each month; older ones are kept.
Corrections to what the pages publish are in fixes.toml and kept on each record they change
(corrections); names cut off in the PDF and registrations listed under the wrong kind are in
_lobbyist_fixes.json. Each record is tied to the state and to the South Dakota cities of the
lobbyist and the employer.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date
from pathlib import Path

import sdnames
from entities.mentions import from_text
from records import fixes
from records.sources import DocumentLink, Found, Item, Source
from records.sources import _lobbyists as L
from records.sources._places import city_place

logger = logging.getLogger(__name__)
STATE = "0400000US46"
FIXES = fixes.load(Path(__file__).with_name("fixes.toml"))


def surnames(names: list[str]) -> list[str]:
    """What to search for each name: its family name as sdnames reads it, and its last word
    (a search finds a compound name either way)."""
    out = set()
    for name in names:
        family = sdnames.person(name, "given_first").family
        words = [w for w in name.split() if w.strip(".,") not in ("JR", "SR", "II", "III", "IV")]
        out.update(x for x in (family, words[-1] if words else "") if x)
    return sorted(out)


def detail_page(fetcher, url: str, year: int, today: date) -> bytes:
    guid = L.cn(url)
    recent = year >= today.year - 1
    name = (f"detail/recent/{today:%Y-%m}/{guid}.html" if recent
            else f"detail/{year}/{guid}.html")  # fmt: skip
    if getattr(fetcher, "store", None) is not None:
        return fetcher.fetch(url, name)
    return fetcher.request("GET", url).content


def places(d: dict) -> list[str]:
    """The state, and the lobbyist's and employer's South Dakota cities."""
    cities = (city_place(d.get(f"{who}_address_city")) for who in ("lobbyist", "employer")
              if d.get(f"{who}_address_state") == "SD")  # fmt: skip
    return [STATE, *dict.fromkeys(c for c in cities if c)]


def check(listed: list[dict], found: list[dict]) -> dict:
    """The PDF's (name, year) pairs against the detail pages'."""
    want, got = defaultdict(int), defaultdict(int)
    for r in listed:
        want[(r["lobbyist_name"], r["year"])] += 1
    for d in found:
        got[(d["lobbyist_name"], str(d["year"]))] += 1
    missing = sorted(f"{n} {y}" for (n, y), c in want.items() if got.get((n, y), 0) < c)
    extra = sorted(f"{n} {y}" for (n, y), c in got.items() if want.get((n, y), 0) < c)
    return {"listed": len(listed), "found": len(found), "missing": missing[:50],
            "missing_count": len(missing), "extra_count": len(extra)}  # fmt: skip


def items(fetcher, today: date | None = None) -> Found:
    today = today or date.today()
    session = L.Session(fetcher)
    session.search()
    pdf = session.printable()
    if getattr(fetcher, "store", None) is not None:
        fetcher.store.save(f"printable/{today:%Y-%m}/private.pdf", pdf, L.SEARCH, method="POST")
    listed = L.pdf_rows(pdf, "private")
    urls = {}
    for surname in surnames(sorted({r["lobbyist_name"] for r in listed})):
        for row in L.result_rows(session.search(last_name=surname)):
            if row["url"]:
                urls.setdefault(row["url"], int(row["year"]))
    public = set(L.FIXES["public_but_private"])
    out, details = Found(), []
    for url, year in sorted(urls.items(), key=lambda x: (-x[1], x[0])):
        if L.cn(url) in public:
            continue  # listed as private, but a public lobbyist's registration
        d = L.detail(detail_page(fetcher, url, year, today), url)
        if corrections := fixes.apply(d, FIXES):
            d["corrections"] = corrections
        details.append(d)
        docs = [DocumentLink(f["filing_url"], metadata={k: v for k, v in f.items()
                                                          if k != "filing_url"})
                for f in d["filings"] if f["filing_url"]]  # fmt: skip
        out.append(Item(key=url, data=d, source_url=url, documents=docs, places=places(d)))
    moved = {(f["lobbyist_name"], f["year"]) for f in L.FIXES["public_but_private"].values()}
    out.checks = check([r for r in listed if (r["lobbyist_name"], r["year"]) not in moved],
                       details)  # fmt: skip
    if out.checks["missing_count"]:
        logger.warning("private lobbyists: %d listed registrations not found",
                       out.checks["missing_count"])  # fmt: skip
    return out


def mentions(record) -> list:
    d, out = record.data, []
    parties = (("lobbyist_name", "lobbyist", "given_first"), ("employer_name", "employer", "auto"),
               ("employer_agent_name", "employer agent", "given_first"))  # fmt: skip
    for field, role, order in parties:
        if d.get(field):
            out += from_text(d[field], dataset=record.recordset.dataset, record_key=str(record),
                             field=field, role=role, order=order, url=record.source_url,
                             start=len(out), context={"year": d["year"]})  # fmt: skip
    return out


def from_legacy(data: dict) -> dict:
    return {"year": data.get("year"), "registration_number": data.get("registration_number")}


SOURCE = Source(slug="private-lobbyists", items=items, mentions=mentions,
                from_legacy=from_legacy)  # fmt: skip
