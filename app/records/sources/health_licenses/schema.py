"""A license on a health licensing board's published list."""

from __future__ import annotations

from pydantic import BaseModel


class License(BaseModel):
    list: str  # the list's slug (boards.LISTS)
    board: str
    profession: str
    holder: str  # "person" or "establishment"
    license_number: str
    license_type: str | None  # as the list gives it ("SLPA", "DC", "Crematory"), or the list's
    name: str  # a person's given then family name, or the establishment's
    last_name: str | None
    first_name: str | None
    city: str | None
    state: str | None
    status: str | None  # where the list gives one ("Active"; the midwives' "Lapsed")
    issued: str | None  # ISO date: the list's effective, issued or original license date
    issued_as_listed: str | None
    expires: str | None  # ISO date
    expires_as_listed: str | None
    discipline: str | None  # the list's discipline column, as printed ("No", "None", "True")
    disciplined: bool | None  # True where it says there has been discipline
    details: dict  # the list's other columns, as printed (address, phone, facility, preceptor)
