"""Session committees: Each session's standing committees and full bodies: members, staff,
bills, agendas, documents. Built by `_legislature.sets.committees` from the crawl's stored
API responses.
"""

from records.sources import Source
from records.sources._legislature import sets

FROM_LEGACY = sets.legacy_fields(
    {
        "session_committee_id": ("SessionCommitteeId", int),
        "committee_id": ("CommitteeId", int),
    }
)

SOURCE = Source(
    slug="legislature-committee-memberships",
    items=sets.committees,
    dataset="legislature",
    publisher="South Dakota Legislature",
    name="Session committees",
    base_url="https://sdlegislature.gov/",
    max_drop=0.05,
    legacy_key=sets.legacy_id,
    from_legacy=FROM_LEGACY,
    description=(
        "Each session's standing committees and full bodies: members, staff, bills, "
        "agendas, documents."
    ),
)
