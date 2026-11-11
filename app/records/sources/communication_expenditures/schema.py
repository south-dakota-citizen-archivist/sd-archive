"""A communication expenditure statement (SDCL 12-27-16): who paid for a communication
mentioning a candidate or ballot question."""

from pydantic import BaseModel


class Statement(BaseModel):
    system: str  # "sdcfr" (filed online, from June 2018) or "archive" (a year page's PDF)
    filer_name: str | None
    filer_id: str | None  # the portal's filer ID
    document_id: str | None
    name_mentioned: str | None
    communication: str | None  # type and description, as listed
    communication_date: str | None
    date_filed: str | None
    year: int | None
    title: str | None  # an archived PDF's link text


VERSION = "1"
