"""A member of a state board or commission, as boardsandcommissions.sd.gov lists them."""

from datetime import date

from pydantic import BaseModel


class Member(BaseModel):
    board_id: int
    board_name: str
    board_website: str | None
    board_phone: str | None
    board_email: str | None
    agency_affiliation: str | None
    agency_website: str | None
    name: str
    position: str | None
    city: str | None
    city_place: str | None  # fully qualified GEOID of the city or CDP, when there's exactly one
    term_end: date | None
    term_end_raw: str
    party: str | None
    bio: str | None
    picture_url: str | None


VERSION = "1"
