"""Campaign finance filings from the Secretary of State's system before sdcfr.sdsos.gov
(2000–2016), when filings had no committee record above them: each filing is its own
record, keyed by its DocGuid, with the year, the filer's name and the committee's name as
the search listed them. The filing (a scanned PDF) is its document.

The system's search is down (2026-11-10), so there's no items() yet; the records came from
the earlier archive (`split_legacy_campaign_finance`). Matching filings to committees and
people is entity work, not structure.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

from entities.mentions import from_text
from records.sources import Source

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


SOURCE = Source(slug="campaign-finance-legacy", mentions=mentions)
