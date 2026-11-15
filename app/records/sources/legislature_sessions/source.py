"""Legislative sessions: Each regular and special session since 1997: its number, year, fiscal
year, documents and dates. Built by `_legislature.sets.sessions` from the crawl's stored API
responses.
"""

from records.sources import Source
from records.sources._legislature import sets

FROM_LEGACY = sets.legacy_fields(
    {
        "session_id": ("SessionId", int),
        "session_year": ("Year", str),
        "session_number": ("SessionNumber", int),
        "fiscal_year": ("FiscalYear", int),
        "is_current_session": ("CurrentSession", bool),
        "is_special_session": ("SpecialSession", bool),
    }
)

SOURCE = Source(
    slug="legislature-sessions",
    items=sets.sessions,
    dataset="legislature",
    publisher="South Dakota Legislature",
    name="Legislative sessions",
    base_url="https://sdlegislature.gov/",
    max_drop=0.05,
    legacy_key=sets.legacy_id,
    from_legacy=FROM_LEGACY,
    description=(
        "Each regular and special session since 1997: its number, year, fiscal year, "
        "documents and dates."
    ),
)
