"""Legislators by session: one record per member's profile for a session (keyed by the
API's SessionMemberId), from the crawl's stored `SessionMembers/Session/{session}` listings
and each member's `SessionMembers/Detail/{id}`. `legislator_id` is the legislator (the
Historical Listing's ID), shared by every session the person served: from the hand-checked
crosswalk (1997–2024), or for later sessions through the API's MemberId, which always
belongs to one legislator (`legislator_id_from` says which).

The API now leaves members' home addresses, phones and email empty. Earlier copies had them:
the legislature data repository (1997–2024) and the records imported from the earlier
system (state email and Capitol phone). Those values are kept in each record's `earlier`,
with where they came from (`_legislature.build.keep_earlier`).

A member is tied to the state, the city of their home address, and (from the 2023 session,
the districts in sdplaces) their House or Senate district.
"""

from __future__ import annotations

import contextlib
import re

from entities.mentions import from_text
from records.sources import Found, Item, Source
from records.sources._legislature.build import (
    SITE,
    STATE,
    api,
    api_url,
    archive,
    archive_url,
    empty,
    keep_earlier,
    last_run,
    legislator_id,
    member_ids,
    stored,
)  # fmt: skip
from records.sources._places import city_place

SLUG = "legislature-legislators-by-term"
CHAMBERS = {"H": "House", "S": "Senate"}
CONTACT = ("home_address1", "home_address2", "home_city", "home_state", "home_zip",
           "home_phone", "capitol_phone", "business_phone", "cell_phone", "email")  # fmt: skip
API_FIELDS = {  # API name → ours
    "HomeAddress1": "home_address1", "HomeAddress2": "home_address2", "HomeCity": "home_city",
    "HomeState": "home_state", "HomeZip": "home_zip", "HomePhone": "home_phone",
    "CapitolPhone": "capitol_phone", "BusinessPhone": "business_phone",
    "CellPhone": "cell_phone", "EmailState": "email",
}  # fmt: skip
REPO_FIELDS = {  # the repository's name → ours
    "address1": "home_address1", "address2": "home_address2", "city": "home_city",
    "state": "home_state", "zipcode": "home_zip", "phone_home": "home_phone",
    "phone_capitol": "capitol_phone", "phone_biz": "business_phone", "phone_cell": "cell_phone",
    "email": "email",
}  # fmt: skip
PRL_FIELDS = {"phone_capitol": "capitol_phone", "phone_home": "home_phone",
              "phone_cell": "cell_phone", "email": "email"}  # fmt: skip
FIRST_DISTRICT_SESSION_YEAR = 2023  # sdplaces has the districts drawn in 2021


def profile_url(member_id: int) -> str:
    return f"{SITE}Legislators/Profile/{member_id}/Detail"


def repo_copy(member_id: int) -> tuple[dict | None, str]:
    name = f"data/legislators/sd-legislature-legislator-{member_id}.json"
    found = archive(name)
    if not found:
        return None, ""
    return {ours: found.get(theirs) for theirs, ours in REPO_FIELDS.items()}, archive_url(name)


def prl_copy(record) -> tuple[dict | None, str]:
    """A record imported from the earlier system, still in its shape."""
    if record is None or "legislator_url" not in record.data:
        return None, ""
    values = {ours: record.data.get(theirs) for theirs, ours in PRL_FIELDS.items()}
    return values, (f"public-records-liberator, from {record.data['legislator_url']} "
                    f"(captured {record.first_seen:%Y-%m-%d})")  # fmt: skip


def member_data(listed: dict, detail: dict | None) -> dict:
    d = {**listed, **(detail or {})}
    data = {
        "session_member_id": d["SessionMemberId"], "member_id": listed.get("MemberId"),
        "person_id": d.get("PersonId"), "session_id": d.get("SessionId"), "year": d.get("Year"),
        "session_year": session_year(d.get("Year")),
        "chamber": CHAMBERS.get(d.get("MemberType"), d.get("MemberType")),
        "name": d.get("Name"), "first_last_name": d.get("FirstLastName"),
        "first_name": listed.get("FirstName"), "last_name": listed.get("LastName"),
        "initial": listed.get("Initial"), "unique_name": d.get("UniqueName"),
        "district": d.get("District"), "district_id": listed.get("DistrictId"),
        "party": d.get("PartyName"), "party_code": d.get("Politics"),
        "term": d.get("MemberTermName"), "term_code": d.get("Term"),
        "occupation": d.get("Occupation"), "counties": d.get("Counties"),
        "position": d.get("Position"), "inactive_date": d.get("InactiveDate"),
        "picture": d.get("Picture"), "picture_small": d.get("PictureSmall"),
        "picture_copyright": d.get("PictureCopyright"),
    }  # fmt: skip
    data |= {ours: d.get(theirs) for theirs, ours in API_FIELDS.items()}
    return data


def session_year(year) -> int | None:
    """The calendar year of a session's year label ("2021", "2021i", "2025s")."""
    digits = re.match(r"\d{4}", str(year or ""))
    return int(digits.group()) if digits else None


def value(data: dict, field: str):
    """The field's value, or the one kept from an earlier copy."""
    if not empty(data.get(field)):
        return data[field]
    return ((data.get("earlier") or {}).get(field) or {}).get("value")


def places(data: dict) -> list[str]:
    import sdplaces

    out = [STATE]
    if city := city_place(value(data, "home_city")):
        out.append(city)
    year = data.get("session_year") or 0
    if year >= FIRST_DISTRICT_SESSION_YEAR and data.get("district"):
        kind = sdplaces.house_district if data["chamber"] == "House" else sdplaces.senate_district
        with contextlib.suppress(sdplaces.UnknownPlace):
            out.append(kind(data["district"]).geoidfq)
    return out


def items(fetcher=None) -> Found:
    before = stored(SLUG)
    sessions = sorted(api("Sessions") or [], key=lambda s: s["SessionId"])
    listings = {s["SessionId"]: api(f"SessionMembers/Session/{s['SessionId']}") or []
                for s in sessions}  # fmt: skip
    by_member_id = member_ids([m for listing in listings.values() for m in listing])
    out = Found()
    checks = {"sessions": 0, "without_detail": 0, "earlier": {}, "legislator_id": {}}
    for listing in listings.values():
        checks["sessions"] += bool(listing)
        for listed in listing:
            mid = listed["SessionMemberId"]
            detail = api(f"SessionMembers/Detail/{mid}")
            checks["without_detail"] += detail is None
            data = member_data(listed, detail)
            data["legislator_id"], data["legislator_id_from"] = legislator_id(
                mid, listed.get("MemberId"), by_member_id
            )
            how = data["legislator_id_from"] or "unknown"
            checks["legislator_id"][how] = checks["legislator_id"].get(how, 0) + 1
            record = before.get(str(mid))
            if record is not None and "legislator_url" in record.data:  # imported, not yet run
                copies, carried = [prl_copy(record), repo_copy(mid)], None
            else:  # newest first: this record set's last run, then the repository
                copies = [last_run(record), repo_copy(mid)]
                carried = record.data.get("earlier") if record else None
            keep_earlier(data, CONTACT, *copies, carried=carried)
            for field in data.get("earlier") or {}:
                checks["earlier"][field] = checks["earlier"].get(field, 0) + 1
            out.append(Item(key=str(mid), data=data, source_url=profile_url(mid),
                            places=places(data)))  # fmt: skip
    out.checks = checks | {"members": len(out)}
    return out


def mentions(record) -> list:
    d = record.data
    address = ", ".join(str(p) for p in (
        value(d, "home_address1"), value(d, "home_address2"), value(d, "home_city"),
        " ".join(str(x) for x in (value(d, "home_state"), value(d, "home_zip")) if x),
    ) if p)  # fmt: skip
    context = {k: d.get(k) for k in ("year", "session_id", "chamber", "district", "party",
                                     "counties", "occupation", "term")}  # fmt: skip
    return from_text(d["name"], dataset=record.recordset.dataset, record_key=str(record),
                     field="name", role="legislator", order="family_first",
                     url=api_url(f"SessionMembers/Detail/{d['session_member_id']}"),
                     context={k: v for k, v in context.items() if v},
                     properties={"address": [address]} if address else {},
                     gold_entity_id=f"sd-legislator:{d['legislator_id']}"
                     if d.get("legislator_id_from") == "crosswalk" else "")  # fmt: skip


def legacy_key(record) -> str:
    """The earlier system keyed by the profile page (…/Legislators/Profile/4731/2026)."""
    m = re.search(r"/Profile/(\d+)", record.key)
    return m.group(1) if m else record.key


def from_legacy(data: dict) -> dict:
    """The earlier system's values in this shape, for comparing (strings it stored as text)."""

    def val(x):
        return None if x in (None, "", "None") else x

    def num(x):
        return int(x) if val(x) is not None else None

    return {
        "session_member_id": num(data.get("legislator_profile_id")),
        "session_id": num(data.get("session_id")), "session_year": num(data.get("session_year")),
        "chamber": val(data.get("chamber")), "name": val(data.get("full_name")),
        "first_name": val(data.get("first_name")), "last_name": val(data.get("last_name")),
        "district": val(data.get("district")), "party": val(data.get("party")),
        "term": val(data.get("term_status")), "occupation": val(data.get("occupation")),
        "counties": val(data.get("counties")), "picture": val(data.get("picture_url")),
        **{ours: val(data.get(theirs)) for theirs, ours in PRL_FIELDS.items()},
    }  # fmt: skip


SOURCE = Source(
    slug=SLUG, items=items, mentions=mentions, legacy_key=legacy_key, from_legacy=from_legacy,
    name="Legislators by session", dataset="legislature", publisher="South Dakota Legislature",
    base_url=f"{SITE}Legislators", max_drop=0.05,
    description="Each legislator's profile for each session since 1997: chamber, district, "
                "party, occupation, counties, and contact details as the Legislature listed "
                "them.",
)  # fmt: skip
