"""The Board of Pardons and Paroles' action reports: every hearing the board decided, one
record each, from the reports it posts with its meetings on boardsandcommissions.sd.gov
(BoardID 74), September 2015 on.

The meetings page lists a year at a time (the year list posts back); a meeting's documents
include its action reports ("… Public All-Action Report", "Board actions …", "Parole Board
Decisions …"). Each report is kept as published (raw/parole-board-actions/reports/) and read
by its layout:

- "Parole Board Decisions by Offender" (2015–2020, text): offender.py. Each case is a ruled
  box with the person's facility, each crime with its county code and transaction number,
  the decision and hearing type, details and next review.
- "Parole Board Actions for Public Release" (2021–2023, text): release.py. A line a case
  with the county courts below it.
- The same, scanned (2024 on): scanned.py, from three OCR engines (ocr.py), a field kept
  where two read it alike. Nine 2015 reports are scans of the older layout, not read yet.

Each report's last page counts its decisions ("Totals Per Hearing Outcome"); the run checks
the hearings read against it, report by report. A report the meetings page stops linking is still
read, from the copy kept (reports-seen.json lists every report the page has listed, with
its meeting). The older reports' county codes are read as
counties by county_codes.py. Records are keyed by report, page, row and hearing.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

import sdplaces
from entities.mentions import from_text
from records.sources import Found, Item, Source

from . import county_codes, listing, ocr, offender, release, scanned
from .schema import Crime, Hearing

SITE = "https://boardsandcommissions.sd.gov/"
MEETINGS = SITE + "Meetings.aspx?BoardID=74"
STATE = "0400000US46"
FIRST_YEAR = 2015
# reports still on the site that the meetings page no longer links (from an earlier list of
# the board's reports); fetched too, so none is lost
KNOWN = [
    ("January 2025 SS Public All Action Report", SITE + "bcuploads/January%202025%20SS%20Public"
                                                        "%20All%20Action%20Report.pdf"),
]  # fmt: skip
SEEN = "reports-seen.json"  # every report the meetings page has listed: url → title, meeting


def meetings(fetcher, today: date | None = None) -> list[listing.Meeting]:
    """Every year's meetings. Years before last are kept as fetched; this year's and last
    year's are fetched again (reports are added to them)."""
    today = today or date.today()
    page = fetcher.request("GET", MEETINGS).text
    out = []
    for year in [y for y in listing.years(page) if y >= FIRST_YEAR]:
        name = f"meetings/{year}.html"
        if year < today.year - 1 and fetcher.store and fetcher.store.has(name):
            html = fetcher.store.read(name)
        else:
            form = {**listing.form(page), "__EVENTTARGET": "ddlArchiveYear",
                    "__EVENTARGUMENT": "", "ddlArchiveYear": str(year)}  # fmt: skip
            html = fetcher.fetch(MEETINGS, name, data=form, refresh=True)
        out += listing.meetings(html)
    return out


def report_name(url: str) -> str:
    return unquote(Path(urlparse(url).path).name)


def _date(text: str | None) -> str | None:
    """ "01-AUG-25" as a date."""
    if not text:
        return None
    try:
        return datetime.strptime(text.strip(), "%d-%b-%y").date().isoformat()
    except ValueError:
        return None


def _county(name: str | None) -> str | None:
    """ "Charles Mix County Court" → "Charles Mix" (None: out of state, or not a county)."""
    if not name:
        return None
    bare = re.sub(r"\s+County(\s+Court)?$", "", name.strip())
    try:
        return sdplaces.county(bare).name.removesuffix(" County")
    except (sdplaces.UnknownPlace, sdplaces.AmbiguousPlace):
        return None


def _places(counties: list[str], when: str | None) -> list[str]:
    out = [STATE]
    for c in counties:
        try:
            out.append(sdplaces.county(c, as_of=when).geoidfq)
        except (sdplaces.UnknownPlace, sdplaces.AmbiguousPlace):
            continue
    return list(dict.fromkeys(out))


def _older(cases: list, meta: dict, codes: dict) -> list[tuple[int, Hearing]]:
    out, rows = [], Counter()
    for case in cases:
        rows[case.page] += 1
        pairs, rest = offender.hearings(case.cells["decision"])
        if rest:
            pairs.append((None, rest))  # kept as printed, unread
        crimes = [Crime(crime=c["crime"], county_code=c["county_code"],
                        county=(codes.get(c["county_code"]) or {}).get("county"),
                        transaction=c["transaction"]) for c in case.crimes]  # fmt: skip
        counties = list(dict.fromkeys(c.county for c in crimes if c.county))
        name = case.cells["name"][0] if case.cells["name"] else None
        race, sex = (case.cells["race"] + [None, None])[:2]
        for i, (decision, kind) in enumerate(pairs or [(None, None)], start=1):
            review = " ".join(case.cells["review"]) or None
            out.append((rows[case.page], Hearing(
                **meta, layout="decisions by offender", read_from="text", page=case.page,
                hearing=i, doc_number=case.cells["doc"][0], name=name, race=race, sex=sex,
                location=" ".join(case.cells["name"][1:]) or None,
                sentence=" ".join(case.cells["os"]) or None, crimes=crimes, counties=counties,
                hearing_type=kind, decision=decision,
                details=" ".join(case.cells["details"]) or None, next_review=review,
                next_review_date=_date(review))))  # fmt: skip
    return out


def _newer(cases: list, meta: dict, read_from: str) -> list[tuple[int, Hearing]]:
    out, rows = [], Counter()
    for c in cases:
        if isinstance(c, release.Case):
            rows[c.page] += 1
            row, fields, unconfirmed, readings = rows[c.page], {
                "doc": c.cells.get("doc"), "name": c.cells.get("name"),
                "race": c.cells.get("race"), "sex": c.cells.get("sex"),
                "county_courts": tuple(c.counties),
                "hearings": tuple((h["hearing_type"], h["decision"], h.get("review", ""))
                                  for h in c.hearings)}, [], {}  # fmt: skip
            page = c.page
        else:  # a scanned case, from scanned.read
            row, fields, unconfirmed, readings = c["row"], c["fields"], c["unconfirmed"], {
                k: {e: list(v) if isinstance(v, tuple) else v for e, v in r.items()}
                for k, r in c["readings"].items()}  # fmt: skip
            page = c["page"]
        courts = list(fields.get("county_courts") or [])
        counties = list(dict.fromkeys(x for x in map(_county, courts) if x))
        hearings = fields.get("hearings") or [(None, None, None)]
        for i, (kind, decision, review) in enumerate(hearings, start=1):
            out.append((row, Hearing(
                **meta, layout="actions for public release", read_from=read_from, page=page,
                hearing=i, doc_number=fields.get("doc"), name=fields.get("name"),
                race=fields.get("race"), sex=fields.get("sex"), county_courts=courts,
                counties=counties, hearing_type=kind or None, decision=decision or None,
                next_review=review or None, next_review_date=_date(review),
                unconfirmed=unconfirmed, readings=readings)))  # fmt: skip
    return out


def items(fetcher) -> Found:
    import pdfplumber

    reports = [(m, d) for m in meetings(fetcher) for d in m.documents if listing.is_report(d)]
    listed = {d.url for _, d in reports}
    found, checks = [], defaultdict(list)
    # every report ever listed, with its meeting: one the page stops linking is still read
    # (from the copy kept), so a link taken down doesn't take its hearings with it
    seen = json.loads(fetcher.store.read(SEEN)) if fetcher.store.has(SEEN) else {}
    for meeting, doc in reports:
        seen[doc.url] = {"title": doc.title, "meeting_date":
                         meeting.date.isoformat() if meeting.date else None}  # fmt: skip
    for title, url in KNOWN:
        seen.setdefault(url, {"title": title, "meeting_date": None})
    for url, info in seen.items():
        if url in listed:
            continue
        when = date.fromisoformat(info["meeting_date"]) if info["meeting_date"] else None
        reports.append((listing.Meeting(when, "", "", "", ""),
                        listing.Document(info["title"], url, "", "")))  # fmt: skip
        checks["reports the meetings page no longer links (read from the copy kept)"].append(
            report_name(url))  # fmt: skip
    fetcher.store.save(SEEN, json.dumps(seen, indent=1, sort_keys=True).encode())
    older_codes, newer_counties = defaultdict(set), defaultdict(set)
    parsed = []  # (meta, kind, payload)
    for meeting, doc in reports:
        name = report_name(doc.url)
        fetcher.fetch(doc.url, f"reports/{name}")
        pdf_path = Path(fetcher.store.root) / "reports" / name
        meta = {"report": name, "report_title": doc.title,
                "meeting_date": meeting.date.isoformat() if meeting.date else None}  # fmt: skip
        with pdfplumber.open(pdf_path) as pdf:
            head = (pdf.pages[0].extract_text() or "").upper()
            last = pdf.pages[-1].extract_text() or ""
            if "BY OFFENDER" in head:
                cases = offender.cases(pdf)
                for c in cases:
                    codes = {x["county_code"] for x in c.crimes if x["county_code"]}
                    if len(codes) == 1:
                        older_codes[c.cells["doc"][0]] |= codes
                parsed.append((meta, "older", cases, offender.summary(last)))
            elif "PUBLIC RELEASE" in head:
                cases = release.cases([(i + 1, pg.extract_words(x_tolerance=1.5))
                                       for i, pg in enumerate(pdf.pages)])  # fmt: skip
                for c in cases:
                    counties = {x for x in map(_county, c.counties) if x}
                    if len(counties) == 1:
                        newer_counties[c.cells.get("doc")] |= counties
                parsed.append((meta, "newer", cases, offender.summary(last)))
            elif ocr.is_scan(pdf_path):
                first = scanned.first_page_text(pdf_path)
                if first is not None and "BY OFFENDER" in first.upper():
                    checks["older-layout scans not read (2015)"].append(name)
                    continue
                got = scanned.read(pdf_path)
                if got is None:
                    checks["scanned reports not read yet (OCR to finish)"].append(name)
                    continue
                cases, info = got
                parsed.append((meta, "scanned", cases, offender.summary(info["summary"])))
            else:
                checks["reports in a layout not read"].append(name)
    codes_seen = Counter(x["county_code"] for meta, kind, cases, _ in parsed if kind == "older"
                         for c in cases for x in c.crimes if x["county_code"])  # fmt: skip
    codes = county_codes.mapping(codes_seen,
                                 county_codes.evidence(older_codes, newer_counties))  # fmt: skip
    for meta, kind, payload, summ in parsed:
        if kind == "older":
            hearings = _older(payload, meta, codes)
        else:
            hearings = _newer(payload, meta, "ocr" if kind == "scanned" else "text")
        expected = sum(summ["outcomes"].values()) or summ["total_inmates"]
        if expected != len(hearings):
            checks["reports whose hearings don't match their own totals"].append(
                {"report": meta["report"], "read": len(hearings),
                 "outcomes": sum(summ["outcomes"].values()),
                 "total_inmates": summ["total_inmates"]})  # fmt: skip
        url = next(d.url for _, d in reports if report_name(d.url) == meta["report"])
        for row, h in hearings:
            found.append(Item(key=f"{h.report}|p{h.page}|{row}|{h.hearing}",
                              data=h.model_dump(mode="json"), source_url=url,
                              places=_places(h.counties, h.meeting_date)))  # fmt: skip
    out = Found(found)
    unsettled = {c: v for c, v in codes.items() if not v["county"]}
    out.checks = {**{k: v for k, v in checks.items()}, "reports": len(reports),
                  "hearings": len(found), "county_codes": codes,
                  "county codes not settled": sorted(unsettled)}  # fmt: skip
    return out


def mentions(record) -> list:
    d = record.data
    if not d.get("name"):
        return []
    return from_text(d["name"], dataset=record.recordset.dataset, record_key=str(record),
                     field="name", role="person before the parole board",
                     order="family_first", url=record.source_url,
                     context={"report": d["report"], "doc_number": d["doc_number"]})  # fmt: skip


SOURCE = Source(
    slug="parole-board-actions", items=items, mentions=mentions, max_drop=0.1,
    name="Parole board actions", dataset="parole-board-actions",
    publisher="South Dakota Board of Pardons and Paroles", base_url=MEETINGS,
    description="Every hearing in the Board of Pardons and Paroles' action reports, 2015 on: "
                "the person (DOC number, name, race, sex), the counties of their sentences, "
                "the hearing type, the board's decision and the next review.",
)  # fmt: skip
