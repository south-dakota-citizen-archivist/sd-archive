"""An entry in the state's public phone directory (sd.gov's phone book)."""

from pydantic import BaseModel


class Entry(BaseModel):
    full_name: str  # as listed; some entries are offices ("DOC-SDSP COMS UNIT A")
    given_name: str | None  # parsed by sdnames, for people
    family_name: str | None
    phone: str | None  # as displayed: "+1 (605) 367-5029"
    phone_e164: str | None  # "+16053675029"
    agencies: list[str]  # the directory's agency filters that list it (usually one)
    agency_ids: list[str]
    division_id: str | None  # the directory's own ID for the entry's unit


VERSION = "1"
