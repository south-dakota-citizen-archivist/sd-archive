"""Itemized disbursements (Schedule B) of South Dakota's federal committees, from the FEC. One
record per transaction (sub_id), linked to the recipient's South Dakota city when there's one."""

from __future__ import annotations

from entities.mentions import from_text
from records.sources import Item, Source, _fec
from records.sources._fec_schedules import schedule
from records.sources.fec_contributions.source import places

FIELDS = ["sub_id", "transaction_id", "file_number", "image_number", "report_type",
          "report_year", "two_year_transaction_period", "line_number", "committee_id",
          "recipient_name", "recipient_city", "recipient_state", "recipient_zip",
          "disbursement_amount", "disbursement_date", "disbursement_description",
          "disbursement_purpose_category", "entity_type", "memo_code", "memo_text",
          "pdf_url"]  # fmt: skip


def row(t: dict) -> dict:
    out = {k: t.get(k) for k in FIELDS}
    out["sub_id"] = str(t["sub_id"])
    out["committee_name"] = (t.get("committee") or {}).get("name")
    return out


def items(fetcher) -> list[Item]:
    out = {}
    for t in schedule(fetcher, "/schedules/schedule_b/", "schedule_b", "disbursement_date"):
        d = row(t)
        url = d["pdf_url"] or f"{_fec.WEB}/disbursements/?committee_id={d['committee_id']}"
        where = places(d, "recipient_city", "recipient_state")
        out[d["sub_id"]] = Item(key=d["sub_id"], data=d, source_url=url, places=where)
    return list(out.values())


def mentions(record) -> list:
    d = record.data
    if not d.get("recipient_name"):
        return []
    return from_text(d["recipient_name"], dataset=record.recordset.dataset,
                     record_key=str(record), field="recipient_name", role="payee",
                     url=record.source_url, context={"committee": d["committee_name"]})  # fmt: skip


def legacy_key(record) -> str:
    return str(record.data.get("sub_id") or record.key)


def from_legacy(data: dict) -> dict:
    return {k: data.get(k) for k in ("recipient_name", "disbursement_amount", "disbursement_date",
                                     "committee_id")}  # fmt: skip


SOURCE = Source(slug="fec-disbursements", items=items, mentions=mentions, complete=False,
                legacy_key=legacy_key, from_legacy=from_legacy)  # fmt: skip
