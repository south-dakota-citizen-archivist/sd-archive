"""Session laws: The session laws enacted each session, with their text. Built by
`_legislature.sets.session_laws` from the crawl's stored API responses.
"""

from records.sources import Source
from records.sources._legislature import sets

FROM_LEGACY = sets.legacy_fields(
    {
        "session_law_id": ("SessionLawId", int),
        "chapter": ("Chapter", int),
        "bill_id": ("BillId", int),
        "catchline": ("Catchline", str),
        "session_id": ("SessionId", int),
    }
)

SOURCE = Source(
    slug="legislature-session-laws",
    items=sets.session_laws,
    dataset="legislature",
    publisher="South Dakota Legislature",
    name="Session laws",
    base_url="https://sdlegislature.gov/",
    max_drop=0.05,
    legacy_key=sets.legacy_id,
    from_legacy=FROM_LEGACY,
    description=("The session laws enacted each session, with their text."),
)
