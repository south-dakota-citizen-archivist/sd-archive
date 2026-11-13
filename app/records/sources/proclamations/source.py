"""Gubernatorial proclamations filed with the Secretary of State, one record per filing,
keyed by its DocGuid: the title and the date. The proclamation (scanned) is the document."""

from records.sources._sos_search import Column, Named, SearchSpec, search_source  # noqa: F401

SPEC = SearchSpec(
    slug="proclamations",
    url="https://sdsos.gov/general-information/executive-actions/gubernatorial-proclamations/search/Default.aspx",
    columns=[Column("Title", "title"), Column("Date", "date", "date")],
    name="Gubernatorial Proclamations",
    description="The governor's proclamations: title and date.",
)  # fmt: skip

SOURCE = search_source(SPEC)
