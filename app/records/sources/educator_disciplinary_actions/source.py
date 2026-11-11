"""Educator disciplinary actions from the Department of Education (Professional Practices and
Standards): certificate applications denied, and certificates suspended or revoked, since
2010, and public reprimands. One record per action, keyed by its file number (DSE 2026-01);
reprimands have none ("Professional Practices" in that column), so they're keyed by date and
name. The decision (a PDF) is its document. Schools may not employ someone whose certificate
is revoked or suspended unless reinstated (SDCL 13-43-5.1)."""

from __future__ import annotations

import re

from entities.mentions import from_text
from records.sources import DocumentLink, Item, Source
from records.sources._tables import LayoutChanged, links, table, text
from sddocs.clean import parse_date

URL = "https://doe.sd.gov/professionalpractices/EDA.aspx"
COLUMNS = ["Name", "File Number", "Action", "Date Effective", "Date Reinstated (if applicable)"]
STATE = "0400000US46"
NOT_REINSTATED = {"", "N/A", "NA", "-"}


def _date(value: str) -> str | None:
    on = parse_date(value) if value and value.upper() not in NOT_REINSTATED else None
    return on.isoformat() if on else None


def rows(html: bytes) -> list[dict]:
    """Every action in the table, in page order. Raises LayoutChanged rather than skip one."""
    out = []
    for n, cells in table(html, COLUMNS):
        name, file_number, action, effective, reinstated = (text(c) for c in cells)
        if not file_number:
            raise LayoutChanged(f"row {n} has no file number")
        files = links(cells[1], URL) or links(cells[0], URL)
        out.append({
            "name": name, "file_number": file_number, "action": action,
            "date_effective": _date(effective), "date_effective_raw": effective,
            "date_reinstated": _date(reinstated), "date_reinstated_raw": reinstated,
            "decision_url": files[0][0] if files else None,
        })  # fmt: skip
    return out


def key(row: dict) -> str:
    if re.fullmatch(r"DSE\s*\d{4}-\d+", row["file_number"]):
        return row["file_number"]
    return f"{row['date_effective'] or row['date_effective_raw']} {row['name']}"


def items(fetcher) -> list[Item]:
    out = {}
    for row in rows(fetcher.request("GET", URL).content):
        docs = [DocumentLink(row["decision_url"], metadata={"label": "Decision"})] if (
            row["decision_url"]) else []  # fmt: skip
        out[key(row)] = Item(key=key(row), data=row,
                                       source_url=row["decision_url"] or URL, documents=docs,
                                       places=[STATE])  # fmt: skip
    return list(out.values())


def mentions(record) -> list:
    d = record.data
    return from_text(d["name"], dataset=record.recordset.dataset, record_key=str(record),
                     field="name", role="educator", order="given_first",
                     url=record.source_url, context={"action": d["action"]})  # fmt: skip


SOURCE = Source(
    slug="educator-disciplinary-actions", items=items, mentions=mentions,
    name="Educator disciplinary actions", dataset="educator-disciplinary-actions",
    publisher="South Dakota Department of Education", base_url=URL, ingest="documents",
    description="Educator certificate applications denied and certificates suspended or "
                "revoked by the Department of Education's Professional Practices and Standards "
                "Commission, since 2010, with each decision.",
)  # fmt: skip
