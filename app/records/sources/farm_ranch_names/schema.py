"""A farm, ranch or home name registration, as the Secretary of State lists it."""

from datetime import date

from pydantic import BaseModel


class Registration(BaseModel):
    name: str
    notes: list[str]  # further lines in the name cell ("Amended 5/17/2010")
    owners: list[str]  # one per line, as written
    date_registered: date | None
    date_registered_raw: str
    counties: list[str]  # as written ("Fall River")
    county_geoids: list[str]  # the counties in force on the date registered


VERSION = "1"
