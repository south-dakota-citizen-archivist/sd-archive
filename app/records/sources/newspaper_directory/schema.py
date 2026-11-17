"""A newspaper in the South Dakota NewsMedia Association's member directory."""

from pydantic import BaseModel


class Newspaper(BaseModel):
    publication_name: str
    city: str  # as listed: "North Sioux City/Dakota Dunes"
    cities: list[str]  # each place the city cell names
    county: str
    website: str | None  # the paper's address; None where the directory gives none
    legal_newspaper: bool | None  # the directory's "Legal Newspaper" (Yes / No)
    legal_newspaper_as_listed: str
    address: str  # mailing address, as listed: "PO Box 367, Plankinton, SD, 57368"
    zip: str | None
    phone: str | None
    email: str | None
    publisher: str  # as listed: "Champion Media SD, LLC / Shaun Sarvis"
    publishers: list[str]  # each company or person it names
