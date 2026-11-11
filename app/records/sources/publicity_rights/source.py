"""Right of publicity registrations for deceased personalities (SDCL 43-7A), from the
Secretary of State's table.

One row per registration, keyed by the date filed and the personality's name. The filing is
the record's document; its fields are read with RegistrationForm. Wrong links on the page
are corrected in fixes.toml.
"""

from __future__ import annotations

from entities.mentions import from_text
from records.sources import DocumentLink, Item, Source

from . import parse
from .schema import RegistrationForm

URL = (
    "https://sdsos.gov/general-information/about-state-south-dakota/"
    "personality_right_publicity.aspx"
)
STATE = "0400000US46"


def key(filed: str, name: str) -> str:
    return f"{filed} {name}"


def items(fetcher) -> list[Item]:
    out = []
    for reg in parse.rows(fetcher.request("GET", URL).content, URL):
        filed = reg.date_filed.isoformat() if reg.date_filed else reg.date_filed_raw
        out.append(Item(
            key=key(filed, reg.personality_name),
            data=reg.model_dump(mode="json"),
            source_url=reg.form_url or URL,
            documents=[DocumentLink(reg.form_url, metadata={"label": "Registration"})]
            if reg.form_url else [],
            places=[STATE],
        ))  # fmt: skip
    return out


def mentions(record) -> list:
    """The personality, and the claimant (organizations and people; several claimants in one
    cell are split by sdnames)."""
    data, common = record.data, {"dataset": record.recordset.dataset, "record_key": str(record),
                                 "url": record.source_url}  # fmt: skip
    out = from_text(data["personality_name"], field="personality_name", role="personality",
                    order="given_first", **common)  # fmt: skip
    out += from_text(data["claimant_name"], field="claimant_name", role="claimant",
                     order="given_first", **common)  # fmt: skip
    return out


def from_legacy(data: dict) -> dict:
    """The liberator kept the three cells as written."""
    return {"personality_name": data.get("personality_name"),
            "claimant_name": data.get("claimant_name"),
            "date_filed_raw": data.get("date_filed")}  # fmt: skip


def legacy_key(record) -> str:
    from sddocs.clean import parse_date

    raw = record.data.get("date_filed") or ""
    on = parse_date(raw) if raw else None
    return key(on.isoformat() if on else raw, record.data.get("personality_name"))


SOURCE = Source(
    slug="publicity-rights", items=items, mentions=mentions,
    categories={"registration": "A right of publicity registration"},
    schemas={"registration": RegistrationForm},
    from_legacy=from_legacy, legacy_key=legacy_key,
)  # fmt: skip
