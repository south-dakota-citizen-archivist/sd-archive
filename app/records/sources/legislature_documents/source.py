"""Legislature documents: every document the Legislature's API lists by type (agendas,
minutes, journals, amendments, fiscal notes, reports, session documents), one record per
document with the listing's details and a link to its file. Built by
`_legislature.sets.documents` from the crawl's stored API responses.
"""

from records.sources import Source
from records.sources._legislature import sets

SOURCE = Source(
    slug="legislature-documents",
    items=sets.documents,
    dataset="legislature",
    publisher="South Dakota Legislature",
    name="Legislature documents",
    base_url="https://sdlegislature.gov/",
    max_drop=0.05,
    description=(
        "Every document the Legislature lists: agendas, minutes, journals, amendments, fiscal"
        " notes, reports and session documents, with links to the files."
    ),
)
