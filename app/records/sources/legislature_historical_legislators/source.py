"""Legislators since statehood: Every legislator since territorial days from the Legislature's
Historical Listing: names, dates, services by chamber and county, offices, and Blue Book
pages. Built by `_legislature.sets.historical_legislators` from the crawl's stored API
responses.
"""

from records.sources import Source
from records.sources._legislature import sets

FROM_LEGACY = sets.legacy_fields(
    {
        "legislator_canonical_id": ("MemberId", int),
        "name_first": ("FirstName", str),
        "name_last": ("LastName", str),
        "name_middle": ("MiddleName", str),
        "gender": ("GenderName", str),
        "birthday": ("Birthdate", str),
        "deathday": ("Deathdate", str),
    }
)

SOURCE = Source(
    slug="legislature-historical-legislators",
    items=sets.historical_legislators,
    dataset="legislature",
    publisher="South Dakota Legislature",
    name="Legislators since statehood",
    base_url="https://sdlegislature.gov/",
    max_drop=0.05,
    mentions=sets.historical_mentions,
    legacy_key=sets.legacy_id,
    from_legacy=FROM_LEGACY,
    description=(
        "Every legislator since territorial days from the Legislature's Historical "
        "Listing: names, dates, services by chamber and county, offices, and Blue Book "
        "pages."
    ),
)
