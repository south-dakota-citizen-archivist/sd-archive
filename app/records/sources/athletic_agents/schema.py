"""An athlete agent's registration: the row the Secretary of State lists, and the fields of
the registration form (SDCL 59-10) it links to."""

from datetime import date
from typing import ClassVar

from pydantic import BaseModel, Field


class Registration(BaseModel):
    agent_name: str  # as listed: "Banks, Chris"
    effective_date: date | None
    expiration_date: date | None
    effective_date_raw: str
    expiration_date_raw: str


class RegistrationForm(BaseModel):
    """Read from the form PDF."""

    VERSION: ClassVar[str] = "1"

    agent_name: str | None = Field(None, description="Full name of the registering agent")
    agent_address: str | None = Field(None, description="Mailing address, as one string")
    agent_phone: str | None = None
    agent_email: str | None = None
    employer_name: str | None = Field(None, description="The agency or employer, if listed")
    employer_address: str | None = None
    sports: list[str] = Field(default_factory=list, description="Sports the agent represents")
    effective_date: date | None = None
    expiration_date: date | None = None
    registration_fee: str | None = Field(None, description='As written, e.g. "$100.00"')
    other_state_license: str | None = Field(
        None, description="The state, when registering on another state's license"
    )
    signature_date: date | None = None


VERSION = "1"
