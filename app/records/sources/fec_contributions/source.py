"""Itemized receipts (Schedule A) of South Dakota's federal committees, from the FEC. One
record per transaction (the FEC's sub_id), linked to the contributor's South Dakota city
when there's one."""

from __future__ import annotations

from entities.mentions import from_text
from records.sources import Found, Item, Source, _fec
from records.sources._fec_schedules import covering, schedule
from records.sources._places import city_place

FIELDS = ["sub_id", "transaction_id", "file_number", "image_number", "report_type",
          "report_year", "two_year_transaction_period", "line_number", "receipt_type_full",
          "entity_type", "contributor_name", "contributor_city", "contributor_state",
          "contributor_zip", "contributor_employer", "contributor_occupation",
          "contribution_receipt_amount", "contribution_receipt_date",
          "contributor_aggregate_ytd", "committee_id", "memo_code", "memo_text",
          "pdf_url"]  # fmt: skip


def row(t: dict) -> dict:
    out = {k: t.get(k) for k in FIELDS}
    out["sub_id"] = str(t["sub_id"])
    out["committee_name"] = (t.get("committee") or {}).get("name")
    out["candidate_id"] = t.get("candidate_id")
    out["candidate_name"] = t.get("candidate_name")
    return out


def places(d: dict, city_key: str, state_key: str) -> list[str]:
    city = city_place(d.get(city_key)) if d.get(state_key) == "SD" else None
    return [_fec.STATE, *([city] if city else [])]


def items(fetcher) -> Found:
    out, read = {}, set()
    for t in schedule(
        fetcher, "/schedules/schedule_a/", "schedule_a", "contribution_receipt_date", read=read
    ):
        d = row(t)
        url = d["pdf_url"] or f"{_fec.WEB}/receipts/?committee_id={d['committee_id']}"
        where = places(d, "contributor_city", "contributor_state")
        out[d["sub_id"]] = Item(key=d["sub_id"], data=d, source_url=url, places=where)
    found = Found(out.values())
    found.covers = covering("fec-contributions", read)
    return found


def mentions(record) -> list:
    d = record.data
    if not d.get("contributor_name"):
        return []
    return from_text(d["contributor_name"], dataset=record.recordset.dataset,
                     record_key=str(record), field="contributor_name", role="contributor",
                     url=record.source_url,
                     context={"committee": d["committee_name"],
                              "employer": d["contributor_employer"]})  # fmt: skip


def legacy_key(record) -> str:
    return str(record.data.get("sub_id") or record.key)


def from_legacy(data: dict) -> dict:
    return {k: data.get(k) for k in ("contributor_name", "contribution_receipt_amount",
                                     "contribution_receipt_date", "committee_id")}  # fmt: skip


SOURCE = Source(slug="fec-contributions", items=items, mentions=mentions,
                legacy_key=legacy_key, from_legacy=from_legacy)  # fmt: skip
