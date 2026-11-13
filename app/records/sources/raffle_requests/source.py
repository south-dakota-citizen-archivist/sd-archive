"""Statewide raffle requests: the notice an organization files with the Secretary of State
before selling tickets for a statewide raffle (SDCL 22-25-25(6)), one record per filing, keyed
by its DocGuid: the year, the organization, the date received and the drawing date. The filed
form (scanned) is the document. The organization is named for matching."""

from records.sources._sos_search import Column, Named, SearchSpec, search_source  # noqa: F401

SPEC = SearchSpec(
    slug="raffle-requests",
    url="https://sdsos.gov/general-information/executive-actions/raffle-requests/Search/Default.aspx",
    columns=[Column("Year", "year", "year"), Column("Organization", "organization"),
             Column("Receive Date", "receive_date", "date"),
             Column("Drawing Date", "drawing_date", "date")],
    named=[Named(("organization",), "raffle sponsor", "auto")],
    name="Statewide Raffle Requests",
    description="Organizations' notices of statewide raffles: who, when filed, when drawn.",
)  # fmt: skip

SOURCE = search_source(SPEC)
