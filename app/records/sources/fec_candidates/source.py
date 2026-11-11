"""Candidates for federal office from South Dakota, from the FEC (one record per candidate ID)."""

from __future__ import annotations

from entities.mentions import from_text
from records.sources import Item, Source, _fec

FIELDS = ["candidate_id", "name", "party", "party_full", "office", "office_full", "state",
          "district", "district_number", "incumbent_challenge_full", "candidate_status",
          "cycles", "election_years", "first_file_date", "last_file_date", "active_through",
          "has_raised_funds", "federal_funds_flag"]  # fmt: skip


def row(c: dict) -> dict:
    return {k: c.get(k) for k in FIELDS}


def items(fetcher) -> list[Item]:
    out = []
    for c in _fec.pages(fetcher, "/candidates/", {"state": "SD", "sort": "name"}, "candidates"):
        out.append(Item(key=c["candidate_id"], data=row(c),
                        source_url=f"{_fec.WEB}/candidate/{c['candidate_id']}/",
                        places=[_fec.STATE]))  # fmt: skip
    return out


def mentions(record) -> list:
    return from_text(record.data["name"], dataset=record.recordset.dataset,
                     record_key=str(record), field="name", role="candidate",
                     url=record.source_url,
                     context={"office": record.data["office_full"]})  # fmt: skip


def legacy_key(record) -> str:
    return record.data.get("candidate_id") or record.key


def from_legacy(data: dict) -> dict:
    return {k: data.get(k) for k in ("candidate_id", "name", "party", "office", "district")}


SOURCE = Source(slug="fec-candidates", items=items, mentions=mentions, legacy_key=legacy_key,
                from_legacy=from_legacy)  # fmt: skip
