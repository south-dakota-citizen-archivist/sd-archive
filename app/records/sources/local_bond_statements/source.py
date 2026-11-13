"""Local governments' bond information statements filed with the Secretary of State, one
record per filing, keyed by its DocGuid: the issuer, the date of issue and the date filed. The
statement (scanned) is the document. The issuer is named for matching."""

from records.sources._sos_search import Column, Named, SearchSpec, search_source  # noqa: F401

SPEC = SearchSpec(
    slug="local-bond-statements",
    url="https://sdsos.gov/general-information/miscellaneous-forms/local-bond-statements/search/",
    columns=[Column("Issuer", "issuer"), Column("Date of Issue", "date_of_issue", "date"),
             Column("Date Filed", "date_filed", "date")],
    named=[Named(("issuer",), "bond issuer", "auto")],
    name="Local Government Bond Information Statements",
    description="Local governments' bond information statements: issuer and dates.",
)  # fmt: skip

SOURCE = search_source(SPEC)
