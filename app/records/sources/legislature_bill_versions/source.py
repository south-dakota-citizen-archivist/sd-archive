"""Bill versions: The text of each version of each bill (introduced, amended, engrossed,
enrolled), as published, with words struck from and added to the law marked. Built by
`_legislature.sets.bill_versions` from the crawl's stored API responses.
"""

from records.sources import Source
from records.sources._legislature import sets

SOURCE = Source(
    slug="legislature-bill-versions",
    items=sets.bill_versions,
    dataset="legislature",
    publisher="South Dakota Legislature",
    name="Bill versions",
    base_url="https://sdlegislature.gov/",
    max_drop=0.05,
    description=(
        "The text of each version of each bill (introduced, amended, engrossed, "
        "enrolled), as published, with words struck from and added to the law marked."
    ),
)
