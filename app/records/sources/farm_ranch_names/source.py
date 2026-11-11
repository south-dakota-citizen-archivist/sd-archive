"""Farm, ranch and home name registrations, from the Secretary of State's table.

One row per registration. Its certificate (a scanned PDF) and any amendment are the record's
documents, and the certificate its primary source. Keyed by the date registered and the
name: names repeat (two BEAVER CREEK RANCHes), and certificate files get renamed
(dakotas-horses.PDF → DakotasHorses.pdf).
"""

from __future__ import annotations

import sdplaces
from entities.mentions import from_text
from records.sources import DocumentLink, Item, Source

from . import parse

URL = "https://sdsos.gov/Business-Services/trademark-name-registration/farm-registrations.aspx"


def items(fetcher) -> list[Item]:
    html = fetcher.request("GET", URL).content
    out = []
    for row in parse.rows(html, URL):
        reg = row.registration
        out.append(Item(
            key=key(reg.date_registered_raw if reg.date_registered is None
                    else reg.date_registered.isoformat(), reg.name),
            data=reg.model_dump(mode="json"),
            source_url=row.certificate_url or URL,
            documents=[DocumentLink(url, metadata={"label": label}) for url, label in row.files],
            places=[sdplaces.get(g, "county").geoidfq for g in reg.county_geoids],
        ))  # fmt: skip
    return out


def key(on: str, name: str) -> str:
    return f"{on} {name}"


def mentions(record) -> list:
    out = []
    for owner in record.data["owners"]:
        out += from_text(owner, dataset=record.recordset.dataset, record_key=str(record),
                         field="owners", role="owner", url=record.source_url,
                         start=len(out))  # fmt: skip
    return out


def from_legacy(data: dict) -> dict:
    """The liberator's rows: owners and counties joined with " / ", the date as ISO (or the
    text it couldn't read), a scrape date. It didn't keep the date as written."""
    from sddocs.clean import parse_date

    raw = data.get("date_registered") or ""
    counties = [c.strip() for c in (data.get("county") or "").split(" / ") if c.strip()]
    on = parse_date(raw) if raw else None
    return {
        "name": data.get("name"),
        "owners": [o.strip() for o in (data.get("owners") or "").split(" / ") if o.strip()],
        "date_registered": on.isoformat() if on else None,
        "counties": counties,
        "county_geoids": parse.county_geoids(counties, on),
    }


def legacy_key(record) -> str:
    data = from_legacy(record.data)
    return key(data["date_registered"] or record.data.get("date_registered") or "", data["name"])


SOURCE = Source(slug="farm-ranch-names", items=items, mentions=mentions,
                from_legacy=from_legacy, legacy_key=legacy_key)  # fmt: skip
