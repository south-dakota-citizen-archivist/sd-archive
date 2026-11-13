"""Oaths of office filed with the Secretary of State, one record per filing, keyed by its
DocGuid: the date, the person's first and last name, the office, whether it's an appointment or
a revocation, the appointing official and a note. The signed oath (scanned) is the document.
The person is named for matching."""

from records.sources._sos_search import Column, Named, SearchSpec, search_source  # noqa: F401

SPEC = SearchSpec(
    slug="oaths-of-office",
    url="https://sdsos.gov/general-information/executive-actions/oaths-of-office/search/",
    columns=[Column("Date", "date", "date"), Column("First Name", "first_name"),
             Column("Last Name", "last_name"), Column("Office", "office"),
             Column("Appoint or Revoke", "appoint_or_revoke"),
             Column("Appointing Official", "appointing_official"), Column("Note", "note")],
    named=[Named(("first_name", "last_name"), "oath of office")],
    name="Oaths of Office",
    description="Oaths of office filed with the Secretary of State: who, which office, when.",
)  # fmt: skip

SOURCE = search_source(SPEC)
