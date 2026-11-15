"""Roll call votes: Every recorded vote on a bill since 1997: the totals and each legislator's
vote. Built by `_legislature.sets.roll_calls` from the crawl's stored API responses.
"""

from records.sources import Source
from records.sources._legislature import sets

FROM_LEGACY = sets.legacy_fields(
    {
        "vote_id": ("VoteId", int),
        "yeas": ("Yeas", int),
        "nays": ("Nays", int),
        "excused": ("Excused", int),
        "absent": ("Absent", int),
        "present": ("Present", int),
        "not_voting": ("NotVoting", int),
        "session_id": ("SessionId", int),
    }
)

SOURCE = Source(
    slug="legislature-roll-calls",
    items=sets.roll_calls,
    dataset="legislature",
    publisher="South Dakota Legislature",
    name="Roll call votes",
    base_url="https://sdlegislature.gov/",
    max_drop=0.05,
    legacy_key=sets.legacy_id,
    from_legacy=FROM_LEGACY,
    description=(
        "Every recorded vote on a bill since 1997: the totals and each legislator's vote."
    ),
)
