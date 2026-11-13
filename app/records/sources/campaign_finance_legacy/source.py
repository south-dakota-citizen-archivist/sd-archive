"""Campaign finance filings from the Secretary of State's system before sdcfr.sdsos.gov
(2000–2016), when filings had no committee record above them: each filing is its own
record, keyed by its DocGuid, with the year, the filer's name and the committee's name as
the search listed them. The filing (a scanned PDF) is its document.

The search lists every filing (a blank search). Matching filings to committees and people is
entity work, not structure.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

from entities.mentions import from_text
from records.sources import Source
from records.sources._sos_search import Column, SearchSpec, from_legacy_for, items_for, legacy_key

STATE = "0400000US46"


def doc_guid(url: str) -> str:
    return parse_qs(urlsplit(url).query)["DocGuid"][0]


def row(metadata: dict, url: str) -> dict:
    """A filing's fields from the search listing."""
    first = (metadata.get("first_name") or "").strip() or None
    last = (metadata.get("last_name") or "").strip() or None
    year = str(metadata.get("year") or "").strip()
    return {
        "doc_guid": doc_guid(url),
        "cab_id": parse_qs(urlsplit(url).query).get("CabId", [None])[0],
        "year": int(year) if year.isdigit() else None,
        "first_name": first,
        "last_name": last,
        "committee_name": (metadata.get("committee_name") or "").strip() or None,
    }


def mentions(record) -> list:
    d, out = record.data, []
    person = " ".join(x for x in (d.get("first_name"), d.get("last_name")) if x)
    named = ((person, "filer", "filer"), (d.get("committee_name"), "committee_name", "committee"))
    for text, field, role in named:
        if text:
            out += from_text(text, dataset=record.recordset.dataset, record_key=str(record),
                             field=field, role=role, order="given_first",
                             url=record.source_url, start=len(out),
                             context={"year": d.get("year")})  # fmt: skip
    return out


SPEC = SearchSpec(
    slug="campaign-finance-legacy",
    url="https://sdsos.gov/elections-voting/campaign-finance/search/",
    columns=[Column("Committee Name", "committee_name"), Column("Last Name", "last_name"),
             Column("First Name", "first_name"), Column("Year", "year", "year")],
)  # fmt: skip

SOURCE = Source(slug="campaign-finance-legacy", items=items_for(SPEC), mentions=mentions,
                publisher="South Dakota Secretary of State", base_url=SPEC.url,
                from_legacy=from_legacy_for(SPEC), legacy_key=legacy_key)  # fmt: skip
