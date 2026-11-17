"""Disciplinary actions by the licensing boards the Department of Health hosts: every action
on a board's disciplinary actions page, and in the Board of Pharmacy's yearly reports. One
record an action, with the board's order (a PDF) as its document where the page links one;
a pharmacy action's document is its year's report.

Each board writes its page its own way (pages.py has a reader a board); pharmacy's reports
are read by reports.py. An entry that looks like an action and isn't read is reported on
the run, never dropped silently. The chiropractic board's yearly complaint logs (counts of
complaints received, investigated and resolved) aren't actions, and aren't read here.
Optometry's, speech-language pathology's and podiatry's pages list their actions back to
2004–2014; the funeral, audiology and nursing facility administrators' pages to 2005.

Records are keyed by board, date (or the year listed) and name. The pages change a few times
a year ("updated quarterly"); a monthly run keeps up.
"""

from __future__ import annotations

import re
from collections import Counter
from urllib.parse import urljoin

from entities.mentions import from_text
from records.sources import DocumentLink, Found, Item, Source
from records.sources._places import city_place
from sddocs.clean import parse_date

from . import pages, reports
from .schema import Action

DOH = "https://doh.sd.gov/licensing-and-records/boards/"
STATE = "0400000US46"
BOARDS = {  # slug → (board, its disciplinary actions page, its entries' reader)
    "optometry": ("Board of Examiners in Optometry",
                  "optometry/complaints/disciplinary-actions/", pages.optometry),
    "speech-language-pathology": ("Board of Examiners for Speech-Language Pathology",
                                  "speech-language-pathology/make-a-complaint/"
                                  "disciplinary-actions/", pages.speech_language_pathology),
    "funeral-service": ("Board of Funeral Service", "funeral/complaints/disciplinary-actions/",
                        pages.funeral),
    "hearing-aid-dispensers-audiologists": ("Board of Hearing Aid Dispensers and Audiologists",
                                            "audiology/complaints/disciplinary-actions/",
                                            pages.audiology),
    "nursing-facility-administrators": ("Board of Nursing Facility Administrators",
                                        "nursing-facility-administrators/complaints/"
                                        "disciplinary-actions/",
                                        pages.nursing_facility_administrators),
    "podiatry": ("Board of Podiatry Examiners", "podiatry/complaints/disciplinary-actions/",
                 pages.podiatry),
    "chiropractic": ("Board of Chiropractic Examiners",
                     "chiropractic/complaints/chiropractic-disciplinary-actions/",
                     pages.chiropractic),
    "pharmacy": ("Board of Pharmacy", "pharmacy/complaints/disciplinary-actions/", None),
}  # fmt: skip
WHOLE_DATE = re.compile(r"^\d{1,2}/\d{1,2}/\d{2,4}$|^[A-Z][a-z]+\.? \d{1,2},? \d{4}$")
DAY_MONTH = re.compile(r"^[A-Z][a-z]+\.? \d{1,2}$")  # "October 30", under its year's heading
REPORT_LINK = re.compile(r"\b(?:19|20)\d{2} disciplinary report\b", re.I)


def _date(text: str | None, year: int | None) -> str | None:
    """A whole date only: "December 2011" gives none (no day is invented); "October 30" takes
    the year it's listed under."""
    text = re.sub(r"(?<=\d)(st|nd|rd|th)\b", "", (text or "").strip().rstrip("."))  # "29th"
    if DAY_MONTH.match(text) and year:
        text = f"{text}, {year}"
    if not WHOLE_DATE.match(text):
        return None
    on = parse_date(text)
    return on.isoformat() if on else None


def action(board: str, fields: dict, *, year: int | None, listed_in: str, text: str) -> dict:
    name, page, _ = BOARDS[board]
    effective = _date(fields.get("effective"), year)
    year = year or (int(effective[:4]) if effective else None)  # no year headings: its date's
    return {
        "board": board, "board_name": name, "name": fields["name"],
        "holder": fields.get("holder") or "person",
        "credential": fields.get("credential"), "license_number": fields.get("license_number"),
        "city": fields.get("city"), "state": fields.get("state"),
        "location": fields.get("location"), "action": fields["action"],
        "effective": effective, "effective_as_listed": fields.get("effective"),
        "ended": _date(fields.get("ended"), year), "ended_as_listed": fields.get("ended"),
        "license_status": fields.get("license_status"), "nature": fields.get("nature"),
        "year": year, "listed_in": listed_in, "text": text,
        "details": fields.get("details") or {},
    }  # fmt: skip


def read_page(board: str, html: str, url: str) -> tuple[list[tuple[dict, list]], list[str]]:
    """A board's page: (action, its documents) for every entry read, and the entries that look
    like actions that weren't."""
    reader = BOARDS[board][2]
    out, unread = [], []
    for e in pages.entries(html):
        if pages.NONE.search(e.flat):
            continue
        fields = reader(e)
        if fields is None:
            if pages.looks_like_an_action(e):
                unread.append(e.flat)
            continue
        docs = [DocumentLink(urljoin(url, href), metadata={"label": label})
                for label, href in e.links if "/media/" in href]  # fmt: skip
        out.append((action(board, fields, year=e.year, listed_in="page", text=e.text), docs))
    return out, unread


def _report_year(label: str, text: str) -> int | None:
    m = re.search(r"\b((?:19|20)\d{2})\b", label) or re.search(r"\b((?:19|20)\d{2})\b", text)
    return int(m[1]) if m else None


def _places(a: dict) -> list[str]:
    out = [STATE]
    if a["city"] and (a["state"] or "SD") == "SD":
        try:
            geoid = city_place(a["city"])
        except Exception:  # several places by the name: nothing guessed
            geoid = None
        if geoid:
            out.append(geoid)
    return out


def key(a: dict) -> str:
    """Board, date and license number (the name where there's none): one pharmacy chain's
    stores are each disciplined under their own permit, on the same day."""
    who = a["license_number"] or a["name"]
    return f"{a['board']}|{a['effective'] or a['effective_as_listed'] or a['year']}|{who}"


def items(fetcher) -> Found:
    import io

    import pdfplumber

    found: dict[str, Item] = {}
    checks: dict = {}
    counts = Counter()
    for board, (_, path, _) in BOARDS.items():
        url = DOH + path
        html = fetcher.text(url, f"pages/{board}.html", refresh=True)
        if board == "pharmacy":
            got, unread = [], []
            for e in pages.entries(html):
                for label, href in e.links:
                    if not REPORT_LINK.search(label):
                        continue
                    report = urljoin(url, href)
                    name = "reports/pharmacy/" + href.rstrip("/").split("/")[-1]
                    content = fetcher.fetch(report, name)
                    with pdfplumber.open(io.BytesIO(content)) as pdf:
                        text = "\n".join(p.extract_text() or "" for p in pdf.pages)
                    read = reports.actions(text)
                    if not read:
                        unread.append(f"{label}: {report}")
                    year = _report_year(label, text)
                    doc = DocumentLink(report, metadata={"label": label})
                    got += [(action(board, a, year=year, listed_in="report", text=a["text"]),
                             [doc]) for a in read if a.get("name") and a.get("action")]  # fmt: skip
        else:
            got, unread = read_page(board, html, url)
        if unread:
            checks[f"{board}: entries not read"] = unread
        for a, docs in got:
            k = key(a)
            same = found.get(k)
            if same is not None and same.data["action"] == a["action"]:
                # one action in two reports (2018's repeats 2016's): one record, both reports
                same.documents += [d for d in docs if d.url not in {x.url for x in same.documents}]
                checks.setdefault("listed in two reports", []).append(k)
                continue
            if same is not None:
                k = f"{k}|{a['action']}"
            if k in found:
                checks.setdefault("listed twice", []).append(k)
                continue
            found[k] = Item(key=k, data=Action(**a).model_dump(mode="json"), source_url=url,
                            documents=docs, places=_places(a))  # fmt: skip
            counts[board] += 1
    out = Found(found.values())
    out.checks = {"actions by board": dict(counts), **checks}
    return out


def mentions(record) -> list:
    d = record.data
    common = {"dataset": record.recordset.dataset, "record_key": str(record),
              "url": record.source_url,
              "context": {"board": d["board_name"], "action": d["action"],
                          "license_number": d["license_number"]}}  # fmt: skip
    if d["holder"] == "establishment":
        import sdnames
        from entities.models import Mention

        schema = sdnames.ftm_schema(d["name"])
        return [Mention(schema=schema if schema in ("Company", "PublicBody") else "Organization",
                        name=d["name"], normalized=sdnames.normalize(d["name"]),
                        properties={}, field="name", position=0, role="disciplined licensee",
                        original=d["name"], **common)]  # fmt: skip
    return from_text(d["name"], field="name", role="disciplined licensee", order="auto",
                     **common)  # fmt: skip


SOURCE = Source(
    slug="health-board-actions", items=items, mentions=mentions, max_drop=0.2,
    name="Health licensing board disciplinary actions", dataset="health-board-actions",
    publisher="South Dakota Department of Health (licensing boards)",
    base_url="https://doh.sd.gov/licensing-and-records/boards/", ingest="documents",
    description="Disciplinary actions by the licensing boards the Department of Health "
                "hosts (optometry, speech-language pathology, funeral service, hearing aid "
                "dispensers and audiologists, nursing facility administrators, podiatry, "
                "chiropractic and pharmacy): who, the action, when, and the board's order.",
)  # fmt: skip
