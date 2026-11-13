"""Newspapers' annual ownership and circulation statements (SDCL 17-2-2.5), one record per
filing, keyed by its DocGuid: the newspaper and the year. The statement (scanned) is the
document. The newspaper is named for matching."""

from records.sources._sos_search import Column, Named, SearchSpec, search_source  # noqa: F401

SPEC = SearchSpec(
    slug="newspaper-circulation",
    url="https://sdsos.gov/general-information/miscellaneous-forms/search/",
    columns=[Column("Name", "newspaper"), Column("Year", "year", "year")],
    named=[Named(("newspaper",), "newspaper", "auto")],
    name="Newspaper Circulation Statements",
    description="Newspapers' annual ownership and circulation statements.",
)  # fmt: skip

SOURCE = search_source(SPEC)
