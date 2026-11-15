"""Legislative Research Council staff: The Legislative Research Council's staff: titles,
groups, the committees and subject areas each one covers. Built by `_legislature.sets.staff`
from the crawl's stored API responses.
"""

from records.sources import Source
from records.sources._legislature import sets

FROM_LEGACY = sets.legacy_fields(
    {
        "person_id": ("PersonId", int),
        "user_id": ("UserId", int),
        "email": ("Email", str),
        "title": ("Title", str),
        "first_name": ("FirstName", str),
        "last_name": ("LastName", str),
    }
)

SOURCE = Source(
    slug="legislature-lrc-staff",
    items=sets.staff,
    dataset="legislature",
    publisher="South Dakota Legislature",
    name="Legislative Research Council staff",
    base_url="https://sdlegislature.gov/",
    max_drop=0.05,
    mentions=sets.staff_mentions,
    legacy_key=sets.legacy_id,
    from_legacy=FROM_LEGACY,
    description=(
        "The Legislative Research Council's staff: titles, groups, the committees and "
        "subject areas each one covers."
    ),
)
