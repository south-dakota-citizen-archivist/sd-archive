"""A deceased personality's right of publicity registration (SDCL 43-7A): the row the
Secretary of State lists, and the fields of the filing it links to."""

from datetime import date
from typing import ClassVar

from pydantic import BaseModel, Field


class Registration(BaseModel):
    personality_name: str  # as listed: "Johnny Carson AKA John W. Carson"
    claimant_name: str  # one claimant or several, as listed
    date_filed: date | None
    date_filed_raw: str  # "December, 20 2017" as written
    form_url: str | None
    corrections: list[dict] = []  # hand corrections applied (fixes.toml)


class RegistrationForm(BaseModel):
    """Read from the filing."""

    VERSION: ClassVar[str] = "1"

    personality_name: str | None = Field(None, description="The deceased personality")
    date_of_death: date | None = None
    claimant_name: str | None = Field(None, description="Who claims the right of publicity")
    claimant_address: str | None = None
    claimant_relationship: str | None = Field(
        None, description="To the deceased: heir, assignee, trustee…"
    )
    filing_date: date | None = None
    registration_number: str | None = Field(None, description="Assigned by the Secretary, if any")
