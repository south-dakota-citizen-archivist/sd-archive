"""Write a run's items into its record set: the latest version of each item only.

For each key, an unchanged item is left alone, a changed one is overwritten (its first_seen
kept), and a new one is inserted. After a complete run, a stored item the run didn't see has
left the source and is deleted. An item's documents follow it: a new file URL adds a pending
Document, and a URL the item no longer lists is removed.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass, field
from itertools import batched

from django.contrib.gis.geos import GEOSGeometry
from django.db import transaction

from records.models import Document, Record, Run
from records.sources import Item

BATCH = 2000


class DropTooLarge(RuntimeError):
    """A complete run found far fewer items than are stored."""


@dataclass
class Stored:
    """The keys a run added, changed, removed and left unchanged."""

    added: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    unchanged: int = 0

    def counts(self) -> dict[str, int]:
        return {"added": len(self.added), "changed": len(self.changed),
                "removed": len(self.removed), "unchanged": self.unchanged}  # fmt: skip

    @property
    def written(self) -> list[str]:
        return self.added + self.changed


def check_drop(previous: int | None, current: int, max_drop: float) -> dict:
    """Refuse to replace a record set when a complete run finds sharply fewer items, which
    usually means a broken source or query rather than real deletions."""
    if not previous:
        return {"drop": None}
    drop = (previous - current) / previous
    if drop > max_drop:
        raise DropTooLarge(
            f"count fell from {previous} to {current} ({drop:.0%}), "
            f"more than max_drop={max_drop:.0%}; run with force if this is expected"
        )
    return {"drop": round(drop, 4)}


def run_hash(items: Iterable[Item]) -> str:
    """One hash for everything a run found, independent of order."""
    digest = hashlib.sha256()
    for h in sorted(i.content_hash for i in items):
        digest.update(h.encode())
    return digest.hexdigest()


def _geometry(item: Item):
    return GEOSGeometry(memoryview(item.geometry), srid=4326) if item.geometry else None


@transaction.atomic
def store(run: Run, items: Iterable[Item], *, complete: bool = True) -> Stored:
    """Store a run's items in its record set. Identical keys collapse to the last one."""
    recordset = run.recordset
    incoming = {i.key: i for i in items}
    current = {
        key: (pk, content_hash)
        for pk, key, content_hash in Record.objects.filter(recordset=recordset).values_list(
            "pk", "key", "content_hash"
        )
    }
    out = Stored()
    out.added = [k for k in incoming if k not in current]
    out.changed = [
        k for k in incoming if k in current and current[k][1] != incoming[k].content_hash
    ]
    out.removed = [k for k in current if k not in incoming] if complete else []
    out.unchanged = len(incoming) - len(out.added) - len(out.changed)

    for chunk in batched([current[k][0] for k in out.removed], BATCH):
        Record.objects.filter(pk__in=chunk).delete()

    updates = [
        Record(
            pk=current[k][0],
            data=incoming[k].data,
            geometry=_geometry(incoming[k]),
            content_hash=incoming[k].content_hash,
            source_url=incoming[k].source_url,
            run=run,
        )  # fmt: skip
        for k in out.changed
    ]
    Record.objects.bulk_update(
        updates, ["data", "geometry", "content_hash", "source_url", "run"], batch_size=BATCH
    )
    Record.objects.bulk_create(
        [
            Record(
                recordset=recordset,
                key=k,
                data=incoming[k].data,
                geometry=_geometry(incoming[k]),
                content_hash=incoming[k].content_hash,
                source_url=incoming[k].source_url,
                first_seen=run.started_at,
                run=run,
            )  # fmt: skip
            for k in out.added
        ],
        batch_size=BATCH,
    )
    _sync_documents(recordset, {k: incoming[k] for k in out.written})
    return out


def _sync_documents(recordset, items: dict[str, Item]) -> None:
    """Make each written record's documents match the files its item lists."""
    if not items:
        return
    if (
        not any(i.documents for i in items.values())
        and not Document.objects.filter(record__recordset=recordset).exists()
    ):
        return  # a record set without files, like a GIS layer
    for chunk in batched(items, BATCH):
        records = dict(
            Record.objects.filter(recordset=recordset, key__in=chunk).values_list("key", "pk")
        )
        existing: dict[int, dict[str, int]] = {}
        for pk, record_id, url in Document.objects.filter(record_id__in=records.values())\
                .exclude(url="").values_list("pk", "record_id", "url"):  # fmt: skip
            existing.setdefault(record_id, {})[url] = pk
        new, gone = [], []
        for key in chunk:
            record_id, links = records[key], items[key].documents
            have = existing.get(record_id, {})
            wanted = {d.url: d for d in links}
            gone += [pk for url, pk in have.items() if url not in wanted]
            new += [
                Document(
                    record_id=record_id, url=d.url, filename=d.filename, metadata=d.metadata
                )  # fmt: skip
                for url, d in wanted.items()
                if url not in have
            ]
        Document.objects.filter(pk__in=gone).delete()
        Document.objects.bulk_create(new, batch_size=BATCH)
