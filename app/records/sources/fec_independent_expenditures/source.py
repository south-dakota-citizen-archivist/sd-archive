"""Independent expenditures (Schedule E) supporting or opposing candidates for South Dakota's
federal offices, from the FEC. One record per transaction (sub_id)."""

from __future__ import annotations

from datetime import date

from entities.mentions import from_text
from records.sources import Item, Source, _fec

FIELDS = ["sub_id", "transaction_id", "file_number", "image_number", "filing_form",
          "committee_id", "candidate_id", "candidate_name", "candidate_office",
          "candidate_office_district", "support_oppose_indicator", "expenditure_amount",
          "expenditure_date", "dissemination_date", "payee_name", "payee_city", "payee_state",
          "expenditure_description", "category_code_full", "memo_code", "memo_text", "cycle",
          "pdf_url"]  # fmt: skip


def row(t: dict) -> dict:
    out = {k: t.get(k) for k in FIELDS}
    out["sub_id"] = str(t["sub_id"])
    out["committee_name"] = (t.get("committee") or {}).get("name")
    return out


def items(fetcher) -> list[Item]:
    month = date.today().strftime("%Y-%m")
    out = {}
    for t in _fec.cursor(fetcher, "/schedules/schedule_e/",
                         {"candidate_office_state": "SD", "sort": "expenditure_date"},
                         f"schedule_e/{month}"):  # fmt: skip
        d = row(t)
        out[d["sub_id"]] = Item(key=d["sub_id"], data=d,
                                source_url=d["pdf_url"] or f"{_fec.WEB}/independent-expenditures/",
                                places=[_fec.STATE])  # fmt: skip
    return list(out.values())


def mentions(record) -> list:
    d, out = record.data, []
    for field, role in (("committee_name", "spender"), ("payee_name", "payee")):
        if d.get(field):
            out += from_text(d[field], dataset=record.recordset.dataset,
                             record_key=str(record), field=field, role=role,
                             url=record.source_url, start=len(out))  # fmt: skip
    return out


def legacy_key(record) -> str:
    return str(record.data.get("sub_id") or record.key)


def from_legacy(data: dict) -> dict:
    return {k: data.get(k) for k in ("candidate_id", "support_oppose_indicator",
                                     "expenditure_amount", "payee_name")}  # fmt: skip


SOURCE = Source(slug="fec-independent-expenditures", items=items, mentions=mentions,
                legacy_key=legacy_key, from_legacy=from_legacy)  # fmt: skip
