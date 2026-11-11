"""Athlete agents registered with the Secretary of State (current registrations only).

One row per registration, keyed by its effective date and the agent's name (a renewal is a
new registration; form file names don't follow the agent: Malinowski's is Lutey.pdf). The
registration form is the record's document; its fields are read with RegistrationForm.
"""

from __future__ import annotations

from entities.mentions import from_text
from records.sources import DocumentLink, Item, Source

from . import parse
from .schema import RegistrationForm

URL = "https://sdsos.gov/general-information/miscellaneous-forms/athletic-agent-registration.aspx"
STATE = "0400000US46"


def key(effective: str, name: str) -> str:
    return f"{effective} {name}"


def items(fetcher) -> list[Item]:
    out = []
    for row in parse.rows(fetcher.request("GET", URL).content, URL):
        reg = row.registration
        on = reg.effective_date.isoformat() if reg.effective_date else reg.effective_date_raw
        out.append(Item(
            key=key(on, reg.agent_name),
            data=reg.model_dump(mode="json"),
            source_url=row.form_url or URL,
            documents=[DocumentLink(row.form_url, metadata={"label": "Registration form"})]
            if row.form_url else [],
            places=[STATE],
        ))  # fmt: skip
    return out


def mentions(record) -> list:
    return from_text(record.data["agent_name"], dataset=record.recordset.dataset,
                     record_key=str(record), field="agent_name", role="athlete agent",
                     url=record.source_url)  # fmt: skip


def from_legacy(data: dict) -> dict:
    """The liberator kept the name and the dates as ISO timestamps, not as written."""
    return {
        "agent_name": data.get("agent_name"),
        "effective_date": (data.get("effective_date") or "")[:10] or None,
        "expiration_date": (data.get("expiration_date") or "")[:10] or None,
    }


def legacy_key(record) -> str:
    data = from_legacy(record.data)
    return key(data["effective_date"] or "", data["agent_name"])


SOURCE = Source(
    slug="athletic-agents", items=items, mentions=mentions,
    categories={"registration-form": "An athlete agent registration form"},
    schemas={"registration-form": RegistrationForm},
    from_legacy=from_legacy, legacy_key=legacy_key,
)  # fmt: skip
