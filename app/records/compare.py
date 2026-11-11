"""What a run of a source would change, without storing anything: for checking a source
against the records already stored (a new parser, a rewritten source, a site that changed)."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from django.db import transaction

from records import sources
from records.models import Record, RecordSet
from records.sources import Item


@dataclass
class Comparison:
    found: int = 0
    stored: int = 0
    same: int = 0
    only_found: list[str] = field(default_factory=list)  # would be added
    only_stored: list[str] = field(default_factory=list)  # would be dropped (complete runs)
    changed: dict[str, list[str]] = field(default_factory=dict)  # key → fields that differ
    examples: dict[str, list[tuple[str, object, object]]] = field(default_factory=dict)

    @property
    def fields(self) -> Counter:
        """How many records differ in each field."""
        return Counter(f for fs in self.changed.values() for f in fs)


def _differences(old: dict, new: dict, keys) -> list[str]:
    return sorted(k for k in keys if old.get(k) != new.get(k))


def compare(slug: str, items: list[Item], *, examples: int = 3) -> Comparison:
    """Compare a source's items with the current stored records, by key and field by field.
    With the source's legacy_key and from_legacy, stored records are matched by their key in
    the source's terms and their data is put in its shape; fields from_legacy leaves out
    weren't kept before, so they aren't compared."""
    source = sources.get(slug)
    rekey = source.legacy_key or (lambda r: r.key)
    stored = {
        rekey(r): r
        for r in Record.objects.current()
        .filter(recordset__slug=slug)
        .only("key", "data", "source_url")
    }
    found = {i.key: i for i in items}
    out = Comparison(found=len(found), stored=len(stored))
    out.only_found = sorted(k for k in found if k not in stored)
    out.only_stored = sorted(k for k in stored if k not in found)
    for key in sorted(found.keys() & stored.keys()):
        record, item = stored[key], found[key]
        old = source.from_legacy(record.data) if source.from_legacy else record.data
        old = {**old, "_source_url": record.source_url}
        new = {**item.data, "_source_url": item.source_url}
        keys = old.keys() if source.from_legacy else old.keys() | new.keys()
        if diff := _differences(old, new, keys):
            out.changed[key] = diff
            for f in diff:
                shown = out.examples.setdefault(f, [])
                if len(shown) < examples:
                    shown.append((key, old.get(f), new.get(f)))
        else:
            out.same += 1
    return out


@dataclass
class Adoption:
    rekeyed: int = 0
    merged: dict[str, list[str]] = field(default_factory=dict)  # new key → old keys folded in


@transaction.atomic
def adopt(slug: str, *, dry_run: bool = False) -> Adoption:
    """Give records imported from an earlier system the source's keys, in place, before the
    source's first run, so that run matches them (keeping when each was first seen) instead
    of adding them again. Imported records that are the same item (the earlier system kept
    one per URL, and the URL changed) merge into the first seen, taking its files; each merged
    record's key, data, URL and dates are kept in the survivor's legacy["merged"]."""
    source = sources.get(slug)
    if source.legacy_key is None:
        raise ValueError(f"{slug}: the source has no legacy_key")
    recordset = RecordSet.objects.get(slug=slug)
    if recordset.runs.exists():
        raise ValueError(f"{slug} has runs already; adopt only imported records, before them")
    groups: dict[str, list[Record]] = {}
    for record in recordset.records.current().order_by("first_seen", "pk"):
        groups.setdefault(source.legacy_key(record), []).append(record)
    out = Adoption()
    for key, (keep, *others) in groups.items():
        if others:
            out.merged[key] = [keep.key, *(o.key for o in others)]
        out.rekeyed += keep.key != key
    if dry_run:
        transaction.set_rollback(True)
        return out
    # two steps, so a new key can't collide with an old one not yet renamed
    for keep, *others in groups.values():
        for other in others:
            urls = set(keep.documents.values_list("url", flat=True))
            other.documents.exclude(url__in=urls).update(record=keep)
            merged = [*keep.legacy.get("merged", []),
                      {"key": other.key, "data": other.data, "source_url": other.source_url,
                       "first_seen": other.first_seen.isoformat(), **other.legacy}]  # fmt: skip
            keep.legacy = {**keep.legacy, "merged": merged}
            other.delete()
        Record.objects.filter(pk=keep.pk).update(key=f"~adopting~{keep.pk}", legacy=keep.legacy)
    for key, (keep, *_) in groups.items():
        Record.objects.filter(pk=keep.pk).update(key=key)
    return out
