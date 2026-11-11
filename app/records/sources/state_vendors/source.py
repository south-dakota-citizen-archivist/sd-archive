"""Vendors the state pays, from the Bureau of Finance and Management's vendor file: each
vendor's number, name, city and state, and what it was paid in the current and two prior
fiscal years. One record per vendor number and group; payments (vendor-checkbook) name
vendors by those numbers."""

from __future__ import annotations

import csv
import io
from datetime import date

from entities.mentions import from_text
from records.sources import Item, Source
from records.sources._places import city_place
from records.sources._tables import LayoutChanged
from records.sources.vendor_checkbook.parse import fiscal_year

URL = "https://bfm.sd.gov/ledger/VendorPmts.csv"
COLUMNS = ["vendor_number", "vendor_group_number", "vendor_name", "city", "state",
           "current_fyr", "prior_fyr_1", "prior_fyr2"]  # fmt: skip
STATE = "0400000US46"


def rows(content: bytes, today: date) -> list[dict]:
    """Vendors, with the file's relative years (current, prior 1, prior 2) as fiscal years."""
    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
    if reader.fieldnames != COLUMNS:
        raise LayoutChanged(f"columns are {reader.fieldnames}")
    now = fiscal_year(today.isoformat())
    out = []
    for r in reader:
        out.append({
            "vendor_number": r["vendor_number"].strip(),
            "vendor_group_number": r["vendor_group_number"].strip(),
            "vendor_name": " ".join(r["vendor_name"].split()),
            "city": " ".join(r["city"].split()) or None, "state": r["state"].strip() or None,
            "paid": {str(now): r["current_fyr"].strip(), str(now - 1): r["prior_fyr_1"].strip(),
                     str(now - 2): r["prior_fyr2"].strip()},
        })  # fmt: skip
    return out


def key(row: dict) -> str:
    group = row["vendor_group_number"]
    return row["vendor_number"] + (f"-{group}" if group else "")


def items(fetcher, today: date | None = None) -> list[Item]:
    out = {}
    for row in rows(fetcher.request("GET", URL, timeout=300).content, today or date.today()):
        city = city_place(row["city"]) if row["state"] == "SD" else None
        out[key(row)] = Item(key=key(row), data=row, source_url=URL,
                             places=[STATE, *([city] if city else [])])  # fmt: skip
    return list(out.values())


def mentions(record) -> list:
    return from_text(record.data["vendor_name"], dataset=record.recordset.dataset,
                     record_key=str(record), field="vendor_name", role="vendor",
                     url=record.source_url, context={"city": record.data["city"]})  # fmt: skip


SOURCE = Source(
    slug="state-vendors", items=items, mentions=mentions,
    name="State vendors", dataset="state-vendors",
    publisher="South Dakota Bureau of Finance and Management", base_url=URL,
    description="Every vendor the state pays: number, name, city and state, and what it was "
                "paid in the current and two prior fiscal years.",
)  # fmt: skip
