"""Elected and appointed officials' financial interest statements, one record per filing,
keyed by its DocGuid: the date, the official's first and last name, the office, the appointing
official, whether it's an appointment or a revocation, and a note. The statement (scanned) is
the document. The official is named for matching."""

from records.sources._sos_search import Column, Named, SearchSpec, search_source  # noqa: F401

SPEC = SearchSpec(
    slug="elected-official-financial-interest",
    url="https://sdsos.gov/general-information/executive-actions/elected-officials-fis/search/Default.aspx",
    columns=[Column("Date", "date", "date"), Column("First Name", "first_name"),
             Column("Last Name", "last_name"), Column("Office", "office"),
             Column("Appointing Official", "appointing_official"),
             Column("Appoint or Revoke", "appoint_or_revoke"), Column("Note", "note")],
    named=[Named(("first_name", "last_name"), "official")],
    name="Elected Official Financial Interest Statements",
    description="Officials' financial interest statements: who, which office, when.",
)  # fmt: skip

SOURCE = search_source(SPEC)
