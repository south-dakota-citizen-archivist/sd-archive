"""Mentions from the names in a record: one per party, typed and parsed by sdnames."""

from collections.abc import Iterable

from django.db import transaction

import sdnames
from entities.models import Mention

# sdnames.person() parts → FtM Person properties
PERSON_PARTS = {"given": "firstName", "middle": "middleName", "family": "lastName",
                "suffix": "nameSuffix", "title": "title"}  # fmt: skip


def person_properties(name: str, order: str = "auto") -> dict:
    p = sdnames.person(name, order)
    return {ftm: [v] for part, ftm in PERSON_PARTS.items() if (v := getattr(p, part))}


def from_text(text: str, *, dataset: str, record_key: str, field: str = "",
              order: str = "auto", role: str = "", **extra) -> list[Mention]:  # fmt: skip
    """Unsaved mentions for every named party in text ("KOLBE, JANE B & ROBERT E" gives two).
    Unnamed parties (ET AL) give none. extra: url, context, jurisdiction, properties,
    valid_from, valid_to, gold_entity_id."""
    shared = extra.pop("properties", {})  # the record's (an address): every party gets them
    out = []
    for position, party in enumerate(sdnames.parties(text, order)):
        if not party.name:
            continue
        schema = sdnames.ftm_schema(party.name)
        properties = person_properties(party.name, order) if schema == "Person" else {}
        properties |= shared
        out.append(Mention(
            schema=schema, name=party.name, normalized=sdnames.normalize(party.name),
            properties=properties, dataset=dataset, record_key=record_key, field=field,
            position=position, role=party.role or role, original=text, **extra,
        ))  # fmt: skip
    return out


@transaction.atomic
def replace(dataset: str, mentions: Iterable[Mention], keys: Iterable[str] | None = None) -> int:
    """Store mentions, replacing what these records (dataset + record_key) had before, so a
    record that changed or lost a party leaves nothing stale. keys: records to clear even if
    they now have no mentions."""
    mentions = list(mentions)
    record_keys = set(keys or ()) | {m.record_key for m in mentions}
    Mention.objects.filter(dataset=dataset, record_key__in=record_keys).delete()
    Mention.objects.bulk_create(mentions, batch_size=2000)
    return len(mentions)
