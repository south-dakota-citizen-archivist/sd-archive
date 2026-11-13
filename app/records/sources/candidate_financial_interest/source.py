"""Candidates' financial interest statements, one record per filing, keyed by its DocGuid:
the candidate's last and first name, the date filed, the office sought, the legislative
district, a note and the committee. The statement (scanned) is the document. The candidate and
the committee are named for matching."""

from records.sources._sos_search import Column, Named, SearchSpec, search_source  # noqa: F401

SPEC = SearchSpec(
    slug="candidate-financial-interest",
    url="https://sdsos.gov/elections-voting/financial-interest-statements/search/default.aspx",
    columns=[Column("Last Name", "last_name"), Column("First Name", "first_name"),
             Column("Date Filed", "date_filed", "date"), Column("Office Sought", "office_sought"),
             Column("Legislative District", "legislative_district"), Column("Note", "note"),
             Column("Committee Name", "committee_name")],
    named=[Named(("first_name", "last_name"), "candidate"),
           Named(("committee_name",), "committee", "auto")],
    name="Candidate Financial Interest Statements",
    description="Candidates' financial interest statements: who, which office, when filed.",
)  # fmt: skip

SOURCE = search_source(SPEC)
