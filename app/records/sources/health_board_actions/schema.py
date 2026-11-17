"""A disciplinary action by a health licensing board."""

from __future__ import annotations

from pydantic import BaseModel


class Action(BaseModel):
    board: str  # the board's slug (source.BOARDS)
    board_name: str
    name: str  # the person or establishment, as printed
    holder: str  # "person", or "establishment" (a pharmacy, under its permit)
    credential: str | None  # as printed: "OD", "Funeral Embalmer", "Hearing Aid Dispenser"
    license_number: str | None
    city: str | None
    state: str | None
    location: str | None  # as printed, where it's more than a town ("Yankton FPC")
    action: str  # as printed: "Voluntary Surrender of License", "12 Month Probation"
    effective: str | None  # ISO date, where the date printed is a whole one
    effective_as_listed: str | None
    ended: str | None  # ISO date: a probation's end, or the license reinstated
    ended_as_listed: str | None
    license_status: str | None  # pharmacy reports: the license's status
    nature: str | None  # the complaint or the violation, as described
    year: int | None  # the year it's listed under
    listed_in: str  # "page" (the board's disciplinary actions page) or "report" (a PDF)
    text: str  # the entry as printed
    details: dict  # anything else the entry gives ("license expired": "06/30/14")
