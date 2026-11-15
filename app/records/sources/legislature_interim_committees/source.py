"""Interim committees: Committees that meet between sessions: members, staff, authority and
documents. Built by `_legislature.sets.interim_committees` from the crawl's stored API
responses.
"""

from records.sources import Source
from records.sources._legislature import sets

FROM_LEGACY = sets.legacy_fields(
    {
        "interim_committee_id": ("InterimYearCommitteeId", int),
    }
)

SOURCE = Source(
    slug="legislature-interim-committees",
    items=sets.interim_committees,
    dataset="legislature",
    publisher="South Dakota Legislature",
    name="Interim committees",
    base_url="https://sdlegislature.gov/",
    max_drop=0.05,
    legacy_key=sets.legacy_id,
    from_legacy=FROM_LEGACY,
    description=("Committees that meet between sessions: members, staff, authority and documents."),
)
