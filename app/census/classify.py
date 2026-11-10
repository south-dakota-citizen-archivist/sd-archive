"""Rules that tag datasets with topics and spot fields that hold people's or org names."""

import hashlib
import json
import re

from census.models import Dataset, DatasetTopic, Topic

# Owner/taxpayer/licensee-style fields. Checked against field names and labels.
NAME_FIELD_RE = re.compile(
    r"owner|ownr|taxpayer|deed_?hold|grantee|grantor|licensee|applicant|contractor"
    r"|business_?name|\bdba\b|deedholder",
    re.IGNORECASE,
)
# ...minus address parts, codes and flags that share those words
NOT_NAME_FIELD_RE = re.compile(
    r"addr|_add\b|add$|stre|street|city|state|_st$|zip|_zi$|cityst|type|code|occ|flag|count"
    r"|coun|num|_?id$|pct|class|ownership|phone|email|date",
    re.IGNORECASE,
)


def fields_hash(fields: list[dict]) -> str:
    canonical = json.dumps(
        sorted((f["name"], f.get("type", "")) for f in fields), separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def suggest_name_fields(fields: list[dict]) -> list[str]:
    found = []
    for f in fields:
        for text in (f["name"], f.get("alias") or ""):
            if NAME_FIELD_RE.search(text) and not NOT_NAME_FIELD_RE.search(text):
                found.append(f["name"])
                break
    return found


def search_text(dataset: Dataset) -> str:
    parts = [dataset.name, dataset.description, dataset.service.name if dataset.service else ""]
    parts += [f"{f['name']} {f.get('alias', '')}" for f in dataset.fields]
    return " ".join(p for p in parts if p)


def matching_topics(dataset: Dataset, topics: list[Topic]) -> set[int]:
    # The layer's own name only: a service called "parcels" holds roads and airports too
    names = dataset.name
    field_texts = [t for f in dataset.fields for t in (f["name"], f.get("alias") or "") if t]
    hits = set()
    for topic in topics:
        name_hit = bool(topic.name_pattern) and bool(
            re.search(topic.name_pattern, names, re.IGNORECASE)
        )
        field_hit = bool(topic.field_pattern) and any(
            re.search(topic.field_pattern, t, re.IGNORECASE) for t in field_texts
        )
        if name_hit or field_hit:
            hits.add(topic.pk)
    return hits


def apply_topics(dataset: Dataset, topics: list[Topic] | None = None) -> None:
    """Replace rule-assigned topics; leave manual ones alone."""
    topics = topics if topics is not None else list(Topic.objects.all())
    wanted = matching_topics(dataset, topics)
    existing = {dt.topic_id: dt for dt in DatasetTopic.objects.filter(dataset=dataset)}
    for topic_id, dt in existing.items():
        if dt.assigned_by == DatasetTopic.AssignedBy.RULE and topic_id not in wanted:
            dt.delete()
    DatasetTopic.objects.bulk_create(
        [DatasetTopic(dataset=dataset, topic_id=t) for t in wanted - set(existing)]
    )
