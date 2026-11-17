"""A hearing in a Board of Pardons and Paroles action report: one decision on one person's
case at one board meeting."""

from __future__ import annotations

from pydantic import BaseModel


class Crime(BaseModel):
    crime: str
    county_code: str | None  # as printed: "PE"
    county: str | None  # the county the code is (county_codes.py), where known
    transaction: str | None


class Hearing(BaseModel):
    report: str  # the report's file name
    report_title: str  # as the board's meetings page lists it
    meeting_date: str | None  # the meeting the board lists the report under
    layout: str  # "decisions by offender" (2015–2020) or "actions for public release"
    read_from: str  # "text", or "ocr" (a scan)
    page: int
    hearing: int  # its place among the hearings the case lists (most list one)
    doc_number: str | None  # the Department of Corrections number
    name: str | None  # as printed: "ABDO, JOHN"
    race: str | None  # as printed: NAT, WHI, BLA, HOL, ASI, OTH, NAH
    sex: str | None
    location: str | None = None  # the facility, in the older reports
    sentence: str | None = None  # OS/NS: NEW or OLD sentence, in the older reports
    crimes: list[Crime] = []  # the older reports only
    county_courts: list[str] = []  # as printed in the newer reports: "Union County Court"
    counties: list[str] = []  # the counties, each once
    hearing_type: str | None
    decision: str | None  # as printed; None where the report gives none (its summary's N/A)
    details: str | None = None  # the older reports only
    next_review: str | None  # as printed: "01-AUG-25"
    next_review_date: str | None  # as a date; DOC prints 01-JAN-50 where there's none to set
    unconfirmed: list[str] = []  # a scan's fields the two OCR engines read differently
    readings: dict = {}  # each engine's reading of those
