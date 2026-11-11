"""The source contract: what a record set's code provides.

Each source lives in `records/sources/<name>/` and defines `SOURCE = Source(...)` in its
`source.py`: how to fetch the items (`items`), and for documents, the categories, rules and
extraction schemas that read them. `get(slug)` finds the source for a record set.
"""

from __future__ import annotations

import hashlib
import importlib
import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pydantic import BaseModel

    from records.models import Document, Record
    from sdfetch import Fetcher


@dataclass
class DocumentLink:
    """A file belonging to an item, where it's published."""

    url: str
    filename: str = ""
    metadata: dict = field(default_factory=dict)


class Found(list):
    """Items, with what the run found out about them: `checks` (logged on the run) and
    `covers` (which stored keys this run speaks for, when that depends on what it fetched)."""

    checks: dict
    covers: Callable[[str], bool] | None = None


@dataclass
class Item:
    """One item at the source, keyed by the source's own key."""

    key: str
    data: dict
    source_url: str = ""
    documents: list[DocumentLink] = field(default_factory=list)
    places: list[str] = field(default_factory=list)  # fully qualified GEOIDs (sdplaces)
    geometry: bytes | None = None  # WKB, EPSG:4326
    content_hash: str = ""

    def __post_init__(self):
        self.documents = [d if isinstance(d, DocumentLink) else DocumentLink(d)
                          for d in self.documents]  # fmt: skip
        if not self.content_hash:
            self.content_hash = item_hash(self)


def item_hash(item: Item) -> str:
    """A hash of everything stored for an item, to tell a changed item from an unchanged one."""
    payload = {
        "data": item.data,
        "source_url": item.source_url,
        "documents": [[d.url, d.filename, d.metadata] for d in item.documents],
    }
    if item.places:
        payload["places"] = sorted(item.places)
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                       default=str).encode())  # fmt: skip
    if item.geometry:
        digest.update(item.geometry)
    return digest.hexdigest()


class Stopped(Exception):  # noqa: N818 (a signal, not an error)
    """Raised by items() to end a run early but keep what it found: the site started refusing
    requests, or the run used its budget. The items are stored as a partial run (nothing is
    marked dropped) and the reason is logged on the run."""

    def __init__(self, items: Iterable[Item], reason: str):
        super().__init__(reason)
        self.checks = dict(getattr(items, "checks", None) or {})  # a Found's own checks
        self.items, self.reason = list(items), reason


@dataclass
class Source:
    """A record set's source.

    - items(fetcher): every item at the source (complete=True), or the new and changed ones
      (complete=False, so items not seen aren't removed).
    - max_drop: a complete run that finds this share fewer items than are stored is refused,
      as a broken source more likely than real deletions (force=True overrides).
    - categories: label → description, for sorting a record set's documents.
    - rule(document): a category decided from the filename, URL or text; None leaves it to
      the model.
    - schemas: category → the Pydantic model extracted from documents of that category;
      "" applies to documents whose category has none of its own. A document without a
      schema has nothing to extract.
    - instructions: extra instructions for the model, by category ("" for all).
    - model: the model for categorizing and extracting (sdllm's default if empty).
    - mentions(record): the named parties in a record, as unsaved entities.Mention rows
      (entities.mentions.from_text) with record_key=str(record), "<record set>:<key>".
    - from_legacy(data): a stored record's data from an earlier system, in this source's
      shape, so `compare_source` can tell real differences from a change of shape.
      legacy_key(record): its key in this source's terms, when the key changed too.
    - name, dataset (catalog/datasets.toml id), publisher (its name), description,
      base_url, ingest: the record set, made on the source's first run if it doesn't exist.
    - covers(key): which stored records a complete run speaks for, when a record set holds
      items from somewhere this source doesn't read (an older system); the rest are never
      marked dropped by it. Default: all of them.
    """

    slug: str
    items: Callable[[Fetcher], Iterable[Item]] | None = None
    complete: bool = True
    max_drop: float = 0.5
    categories: dict[str, str] = field(default_factory=dict)
    rule: Callable[[Document], str | None] | None = None
    schemas: dict[str, type[BaseModel]] = field(default_factory=dict)
    instructions: dict[str, str] = field(default_factory=dict)
    model: str = ""
    mentions: Callable[[Record], list] | None = None
    from_legacy: Callable[[dict], dict] | None = None
    legacy_key: Callable[[Record], str] | None = None
    covers: Callable[[str], bool] | None = None
    # The record set, for a source new to the archive (made on its first run)
    name: str = ""
    dataset: str = ""
    publisher: str = ""
    description: str = ""
    base_url: str = ""
    ingest: str = "rows"

    def schema_for(self, category: str) -> type[BaseModel] | None:
        return self.schemas.get(category) or self.schemas.get("")


_registered: dict[str, Source] = {}


def register(source: Source) -> Source:
    _registered[source.slug] = source
    return source


def unregister(slug: str) -> None:
    _registered.pop(slug, None)


def get(slug: str) -> Source:
    """The source for a record set: a registered one, or `records.sources.<slug>.source`."""
    if slug in _registered:
        return _registered[slug]
    module = importlib.import_module(f"records.sources.{slug.replace('-', '_')}.source")
    return register(module.SOURCE)
