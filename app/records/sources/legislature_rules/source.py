"""Administrative rules: South Dakota's administrative rules, article by article, chapter by
chapter and rule by rule, as currently published. Built by `_legislature.sets.rules` from
the crawl's stored API responses.
"""

from records.sources import Source
from records.sources._legislature import sets

SOURCE = Source(
    slug="legislature-rules",
    items=sets.rules,
    dataset="legislature",
    publisher="South Dakota Legislature",
    name="Administrative rules",
    base_url="https://sdlegislature.gov/",
    max_drop=0.05,
    description=(
        "South Dakota's administrative rules, article by article, chapter by chapter and "
        "rule by rule, as currently published."
    ),
)
