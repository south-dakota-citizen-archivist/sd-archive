"""Political committees registered in South Dakota, from the FEC (one record per committee ID)."""

from __future__ import annotations

from entities.mentions import from_text
from records.sources import Item, Source, _fec

FIELDS = ["committee_id", "name", "committee_type", "committee_type_full", "designation",
          "designation_full", "organization_type_full", "party", "party_full", "state",
          "treasurer_name", "candidate_ids", "cycles", "first_file_date", "last_file_date",
          "filing_frequency"]  # fmt: skip


def row(c: dict) -> dict:
    return {k: c.get(k) for k in FIELDS}


def items(fetcher) -> list[Item]:
    out = []
    for c in _fec.pages(fetcher, "/committees/", {"state": "SD", "sort": "name"}, "committees"):
        out.append(Item(key=c["committee_id"], data=row(c),
                        source_url=f"{_fec.WEB}/committee/{c['committee_id']}/",
                        places=[_fec.STATE]))  # fmt: skip
    return out


def mentions(record) -> list:
    d = record.data
    out = from_text(d["name"], dataset=record.recordset.dataset, record_key=str(record),
                    field="name", role="committee", url=record.source_url)  # fmt: skip
    if d.get("treasurer_name"):
        out += from_text(d["treasurer_name"], dataset=record.recordset.dataset,
                         record_key=str(record), field="treasurer_name", role="treasurer",
                         url=record.source_url, start=len(out))  # fmt: skip
    return out


def legacy_key(record) -> str:
    return record.data.get("committee_id") or record.key


def from_legacy(data: dict) -> dict:
    return {k: data.get(k) for k in ("committee_id", "name", "committee_type", "designation",
                                     "party", "treasurer_name")}  # fmt: skip


SOURCE = Source(slug="fec-committees", items=items, mentions=mentions, legacy_key=legacy_key,
                from_legacy=from_legacy)  # fmt: skip
