"""Codified laws and constitution: South Dakota Codified Laws, title by title, chapter by
chapter and section by section, and the state constitution, as currently published. Built by
`_legislature.sets.statutes` from the crawl's stored API responses.
"""

from records.sources import Source
from records.sources._legislature import sets

SOURCE = Source(
    slug="legislature-statutes",
    items=sets.statutes,
    dataset="legislature",
    publisher="South Dakota Legislature",
    name="Codified laws and constitution",
    base_url="https://sdlegislature.gov/",
    max_drop=0.05,
    description=(
        "South Dakota Codified Laws, title by title, chapter by chapter and section by "
        "section, and the state constitution, as currently published."
    ),
)
