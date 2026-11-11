"""Public lobbyists (state and local government employees who lobby), one per lobbyist,
agency and year, from the Secretary of State's printer-friendly list: the only form the
site gives them in (no detail pages). Keyed by year, name and agency: one person can
lobby for several agencies in a year. Two registrations the site lists as private are
public ones (_lobbyist_fixes.json) and are added here. Each record is tied to the state and
to the agency's South Dakota city."""

from __future__ import annotations

from datetime import date

from entities.mentions import from_text
from records.sources import Item, Source
from records.sources import _lobbyists as L
from records.sources._places import city_place

STATE = "0400000US46"


def key(row: dict) -> str:
    return f"{row['year']} {row['lobbyist_name']} | {row['agency']}"


def items(fetcher, today: date | None = None) -> list[Item]:
    today = today or date.today()
    session = L.Session(fetcher)
    session.choose("public")
    session.search()
    pdf = session.printable()
    if getattr(fetcher, "store", None) is not None:
        fetcher.store.save(f"printable/{today:%Y-%m}/public.pdf", pdf, L.SEARCH, method="POST")
    rows = L.pdf_rows(pdf, "public")
    for fix in L.FIXES["public_but_private"].values():
        rows.append({"year": fix["year"], "lobbyist_name": fix["lobbyist_name"],
                     "lobbyist_name_address": "", "agency": fix["agency"],
                     "agency_address": fix["agency_address"], "listed_as": "private",
                     **L.address_parts(fix["agency_address_lines"], "agency_address")})  # fmt: skip
    out = {}
    for row in rows:
        row = {**row, "year": int(row["year"])}
        city = (city_place(row.get("agency_address_city"))
                if row.get("agency_address_state") == "SD" else None)  # fmt: skip
        out[key(row)] = Item(key=key(row), data=row, source_url=L.SEARCH,
                             places=[STATE, *([city] if city else [])])  # fmt: skip
    return list(out.values())


def mentions(record) -> list:
    d = record.data
    common = {
        "dataset": record.recordset.dataset,
        "record_key": str(record),
        "url": record.source_url,
        "context": {"year": d["year"]},
    }
    out = from_text(d["lobbyist_name"], field="lobbyist_name", role="lobbyist",
                    order="given_first", **common)  # fmt: skip
    out += from_text(d["agency"], field="agency", role="agency", start=len(out), **common)
    return out


def legacy_key(record) -> str:
    d = record.data
    name, agency = (d.get("lobbyist_name") or "").upper(), (d.get("agency") or "").upper()
    return f"{d.get('year')} {name} | {agency}"


def from_legacy(data: dict) -> dict:
    return {"lobbyist_name": (data.get("lobbyist_name") or "").upper(),
            "agency": (data.get("agency") or "").upper()}  # fmt: skip


SOURCE = Source(slug="public-lobbyists", items=items, mentions=mentions,
                legacy_key=legacy_key, from_legacy=from_legacy)  # fmt: skip
