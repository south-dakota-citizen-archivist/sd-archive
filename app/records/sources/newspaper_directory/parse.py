"""Read the member directory: the table the directory page shows, as its widget's API
returns it (columns and rows), and what the page says about where to get it."""

from __future__ import annotations

import re

from .schema import Newspaper

COLUMNS = ["city", "county", "website", "publication-name", "legal-newspaper",
           "address-information", "phone-number", "general-email", "publisher"]  # fmt: skip


class LayoutChanged(Exception):
    pass


def widget(page_html: str) -> dict:
    """Where the directory's table comes from: the table's ID, the host its widget calls and
    the widget app's ID (whose access token the call needs)."""
    table = re.search(r'<data-tables-table[^>]*table-id="([0-9a-f-]{36})"', page_html)
    app = re.search(r'"appDefinitionName":"Data Tables[^"]*","appDefinitionId":"([0-9a-f-]{36})"',
                    page_html)  # fmt: skip
    host = re.search(r"([a-z0-9-]+\.wix-host\.com)", page_html)
    if not (table and app and host):
        raise LayoutChanged("the directory page no longer names its table widget")
    return {"table": table[1], "app": app[1], "host": host[1]}


def _clean(value: str | None) -> str:
    return " ".join((value or "").split())


def newspapers(table: dict) -> list[Newspaper]:
    columns = [c["id"] for c in table["snapshot"]["columns"]]
    if columns != COLUMNS:
        raise LayoutChanged(f"the table's columns changed: {columns}")
    out = []
    for row in table["snapshot"]["rows"]:
        if len(row) != len(COLUMNS):
            raise LayoutChanged(f"a row has {len(row)} cells, not {len(COLUMNS)}: {row}")
        r = {c: _clean(v) for c, v in zip(COLUMNS, row, strict=True)}
        website = r["website"] if re.match(r"https?://", r["website"]) else None
        legal = {"yes": True, "no": False}.get(r["legal-newspaper"].lower())
        zip_code = re.search(r"\b(\d{5})(?:-\d{4})?\s*$", r["address-information"])
        out.append(Newspaper(
            publication_name=r["publication-name"], city=r["city"],
            cities=[c.strip() for c in r["city"].split("/") if c.strip()],
            county=r["county"], website=website, legal_newspaper=legal,
            legal_newspaper_as_listed=r["legal-newspaper"], address=r["address-information"],
            zip=zip_code[1] if zip_code else None, phone=r["phone-number"] or None,
            email=r["general-email"] or None, publisher=r["publisher"],
            publishers=[p.strip() for p in re.split(r"\s+[/|]\s+", r["publisher"]) if p.strip()],
        ))  # fmt: skip
    return out
