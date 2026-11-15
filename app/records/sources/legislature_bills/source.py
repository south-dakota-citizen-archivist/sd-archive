"""Bills: Every bill and resolution since 1997, with its versions, action log, amendments,
fiscal notes, hearing audio index and conference committees. Built by
`_legislature.sets.bills` from the crawl's stored API responses.
"""

from records.sources import Source
from records.sources._legislature import sets

FROM_LEGACY = sets.legacy_fields(
    {
        "bill_id": ("BillId", int),
        "bill_number": ("BillNumber", int),
        "bill_title": ("Title", str),
        "session_id": ("SessionId", int),
        "bill_type": ("BillTypeFull", str),
    }
)

SOURCE = Source(
    slug="legislature-bills",
    items=sets.bills,
    dataset="legislature",
    publisher="South Dakota Legislature",
    name="Bills",
    base_url="https://sdlegislature.gov/",
    max_drop=0.05,
    legacy_key=sets.legacy_id,
    from_legacy=FROM_LEGACY,
    description=(
        "Every bill and resolution since 1997, with its versions, action log, amendments,"
        " fiscal notes, hearing audio index and conference committees."
    ),
)
