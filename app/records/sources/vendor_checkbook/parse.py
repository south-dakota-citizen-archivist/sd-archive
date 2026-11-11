"""The checkbook files (CSV) and the agency list → payment rows. Pure."""

from __future__ import annotations

import csv
import hashlib
import io
from decimal import Decimal, InvalidOperation

from records.sources._tables import LayoutChanged, document, text

COLUMNS = ["agency", "document_date", "document_number", "vendor_name", "vendor_number",
           "vendor_group_number", "ap_payment_date", "voucher_number", "amt"]  # fmt: skip

__all__ = ["COLUMNS", "LayoutChanged", "agencies", "fiscal_year", "key", "rows"]


def _iso(value: str) -> str | None:
    """20260807 → 2026-08-07 (the files' form); an ISO date passes through."""
    value = value.strip()
    if len(value) == 8 and value.isdigit():
        return f"{value[:4]}-{value[4:6]}-{value[6:]}"
    return value[:10] or None


def amount(value: str) -> str:
    """An amount with two decimal places (the files write 88.00; copies made by spreadsheet
    software, 88.0), so the same payment always hashes the same."""
    try:
        return f"{Decimal(value.strip()):.2f}"
    except InvalidOperation:
        return value.strip()


def fiscal_year(iso_date: str) -> int:
    """South Dakota's fiscal year runs July through June: FY2027 starts July 1, 2026."""
    year, month = int(iso_date[:4]), int(iso_date[5:7])
    return year + 1 if month >= 7 else year


def rows(content: bytes) -> list[dict]:
    """Every payment in a checkbook file, with its fields as published (dates as ISO)."""
    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
    if reader.fieldnames != COLUMNS:
        raise LayoutChanged(f"columns are {reader.fieldnames}")
    out = []
    for n, r in enumerate(reader, start=2):
        if not r["ap_payment_date"] or not r["agency"]:
            raise LayoutChanged(f"line {n}: no payment date or agency")
        out.append({
            "agency": r["agency"].strip(), "document_date": _iso(r["document_date"]),
            "document_number": r["document_number"].strip(),
            "vendor_name": " ".join(r["vendor_name"].split()),
            "vendor_number": r["vendor_number"].strip(),
            "vendor_group_number": r["vendor_group_number"].strip(),
            "ap_payment_date": _iso(r["ap_payment_date"]),
            "voucher_number": r["voucher_number"].strip(), "amount": amount(r["amt"]),
        })  # fmt: skip
    return out


def key(row: dict, occurrence: int = 1) -> str:
    """A payment has no ID of its own (only the whole row is unique), so: its payment date,
    agency and voucher, readable and in date order, and a hash of every field. A row that
    appears twice, identical, is told apart by its occurrence."""
    fields = [row[f] or "" for f in ("agency", "document_date", "document_number",
                                     "vendor_name", "vendor_number", "vendor_group_number",
                                     "ap_payment_date", "voucher_number", "amount")]  # fmt: skip
    digest = hashlib.sha256("\x1f".join(fields).encode()).hexdigest()[:12]
    suffix = f"-{occurrence}" if occurrence > 1 else ""
    return f"{row['ap_payment_date']} {row['agency']} {row['voucher_number']} {digest}{suffix}"


def agencies(html: bytes) -> dict[str, str]:
    """Agency code → name, from the state's agency contact list (the last table)."""
    tables = document(html).cssselect("table")
    if not tables:
        raise LayoutChanged("no agency table")
    out = {}
    for tr in tables[-1].cssselect("tr")[1:]:
        cells = [text(td) for td in tr.findall("td")]
        if len(cells) >= 3 and cells[0]:
            out[cells[0]] = cells[2]
    return out
