"""seeds/sources.toml: publishers, sources and watched datasets.

`load()` reads the file into the database (safe to re-run). `dump()` writes the database
back to the file, and signals call it after any change, so the file always matches.
"""

import contextlib
import json
import re
import tomllib
from pathlib import Path

from django.conf import settings
from django.db import connection, transaction
from django.utils.text import slugify

from census.models import Dataset, Publisher, Source
from places.models import Jurisdiction

SEED_FILE = Path(settings.BASE_DIR) / "seeds" / "sources.toml"

HEADER = """\
# Known sources, their publishers, and datasets to watch.
#
# The app rewrites this file whenever a source, publisher or watched dataset changes, so it
# always matches the database. `uv run python manage.py seed` loads it, e.g. into a rebuilt
# database. If you edit it by hand, run `seed` before changing anything in the app, or the
# app will overwrite your edits. Jurisdictions are referenced by slug, e.g.
# "lawrence-county", "sioux-falls", "south-dakota". Fields at their defaults are left out.
"""
WATCH_HEADER = "# Datasets to harvest on a schedule. Matched by URL once a crawl has found them."

# Optional settings, written only when they differ from the model's default
SOURCE_SETTINGS = ("active", "crawl_schedule", "crawl_counts", "max_workers", "time_limit_minutes")
WATCH_SETTINGS = ("max_drop", "where", "batch_size", "harvest_schedule")

# Saves that touch only other fields (crawl times, counts, errors) can't change the file
SEEDED_FIELDS = {
    Publisher: {"name", "jurisdiction", "website", "notes"},
    Source: {
        "name",
        "slug",
        "url",
        "platform",
        "publisher",
        "jurisdiction",
        "folder_jurisdictions",
        "found_via",
        "notes",
        *SOURCE_SETTINGS,
    },
    Dataset: {"status", "name_fields", "name_fields_manual", *WATCH_SETTINGS},
}

_suspended = False


class SeedError(ValueError):
    pass


def jurisdiction(slug: str | None) -> Jurisdiction | None:
    if not slug:
        return None
    try:
        return Jurisdiction.objects.get(slug=slug)
    except Jurisdiction.DoesNotExist as e:
        raise SeedError(
            f"unknown jurisdiction {slug!r}; run `manage.py load_jurisdictions` first"
        ) from e


@contextlib.contextmanager
def suspended():
    """Don't rewrite the file for changes made inside this block."""
    global _suspended
    before, _suspended = _suspended, True
    try:
        yield
    finally:
        _suspended = before


def load(path: Path | None = None) -> dict[str, int]:
    with suspended(), transaction.atomic():
        return _load(path or SEED_FILE)


def _load(path: Path) -> dict[str, int]:
    data = tomllib.loads(path.read_text())

    publishers = {}
    for p in data.get("publishers", []):
        publishers[p["name"]], _ = Publisher.objects.update_or_create(
            name=p["name"],
            defaults={
                "slug": slugify(p["name"])[:255],
                "jurisdiction": jurisdiction(p.get("jurisdiction")),
                "website": p.get("website", ""),
                "notes": p.get("notes", ""),
            },
        )

    for s in data.get("sources", []):
        folders = s.get("folder_jurisdictions", {})
        for slug in folders.values():
            jurisdiction(slug)  # validate
        Source.objects.update_or_create(
            url=s["url"].rstrip("/"),
            defaults={
                "name": s["name"],
                "slug": s.get("slug") or slugify(s["name"])[:255],
                "platform": s["platform"],
                "publisher": publishers.get(s.get("publisher")),
                "jurisdiction": jurisdiction(s.get("jurisdiction")),
                "folder_jurisdictions": folders,
                "found_via": s.get("found_via", ""),
                "notes": s.get("notes", ""),
                **{k: s[k] for k in SOURCE_SETTINGS if k in s},
            },
        )

    watched = 0
    for w in data.get("watch", []):
        updates = {"status": Dataset.Status.WATCHED}
        updates |= {k: w[k] for k in WATCH_SETTINGS if k in w}
        if "name_fields" in w:
            updates |= {"name_fields": w["name_fields"], "name_fields_manual": True}
        watched += Dataset.objects.filter(url=w["url"].rstrip("/")).update(**updates)

    return {
        "publishers": len(publishers),
        "sources": len(data.get("sources", [])),
        "watched": watched,
        "watch_entries": len(data.get("watch", [])),
    }


# Writing


def toml_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(toml_value(v) for v in value) + "]"
    # JSON string escapes are valid in TOML basic strings
    return json.dumps(str(value), ensure_ascii=False)


def toml_key(key: str) -> str:
    return key if re.fullmatch(r"[A-Za-z0-9_-]+", key) else toml_value(key)


def table(header: str, values: dict) -> str:
    lines = [header] + [f"{toml_key(k)} = {toml_value(v)}" for k, v in values.items()]
    return "\n".join(lines) + "\n"


def compact(values: dict) -> dict:
    """Drop blank text and missing links, which load() treats as blank anyway."""
    return {k: v for k, v in values.items() if v not in ("", None)}


def non_default(obj, names) -> dict:
    """The named fields whose values differ from the model's default."""
    return {
        name: getattr(obj, name)
        for name in names
        if getattr(obj, name) != obj._meta.get_field(name).get_default()
    }


def kept_watch_entries(path: Path) -> list[dict]:
    """Watch entries for datasets the database hasn't found yet, e.g. right after a rebuild,
    before the first crawl. Without this, seeding an empty database would drop them."""
    if not path.exists():
        return []
    entries = tomllib.loads(path.read_text()).get("watch", [])
    known = set(Dataset.objects.values_list("url", flat=True))
    return [w for w in entries if w["url"].rstrip("/") not in known]


def render(path: Path | None = None) -> str:
    path = path or SEED_FILE
    sources = list(
        Source.objects.filter(platform__in=Source.CRAWLABLE)
        .select_related("publisher", "jurisdiction")
        .order_by("pk")
    )
    used = {s.publisher_id for s in sources}
    # map apps' publishers come from seeds/map-apps.html instead
    publishers = [
        p
        for p in Publisher.objects.select_related("jurisdiction").order_by("pk")
        # publishers only of record sets belong to the records app, not this file
        if p.pk in used or not (p.sources.exists() or p.recordsets.exists())
    ]

    parts = [HEADER]
    for p in publishers:
        values = compact(
            {
                "name": p.name,
                "jurisdiction": p.jurisdiction.slug if p.jurisdiction else "",
                "website": p.website,
                "notes": p.notes,
            }
        )
        parts.append(table("[[publishers]]", values))

    for s in sources:
        values = compact(
            {
                "name": s.name,
                "slug": s.slug,
                "url": s.url,
                "platform": s.platform,
                "publisher": s.publisher.name if s.publisher else "",
                "jurisdiction": s.jurisdiction.slug if s.jurisdiction else "",
                "found_via": s.found_via,
                "notes": s.notes,
            }
        ) | non_default(s, SOURCE_SETTINGS)
        text = table("[[sources]]", values)
        if s.folder_jurisdictions:
            # Postgres reorders JSON keys, so sort them for a stable file
            places = dict(sorted(s.folder_jurisdictions.items(), key=lambda kv: kv[0].lower()))
            text += "\n" + table("[sources.folder_jurisdictions]", places)
        parts.append(text)

    watches = []
    for d in Dataset.objects.filter(status=Dataset.Status.WATCHED).order_by("pk"):
        values = {"url": d.url, **non_default(d, WATCH_SETTINGS)}
        if d.name_fields_manual:
            values["name_fields"] = d.name_fields
        watches.append(values)
    watches += kept_watch_entries(path)
    parts.append(WATCH_HEADER + "\n" + "\n".join(table("[[watch]]", w) for w in watches))

    return "\n".join(parts)


def dump(path: Path | None = None) -> bool:
    """Write the database to the seed file. Returns False if nothing changed."""
    path = path or SEED_FILE
    text = render(path)
    if path.exists() and path.read_text() == text:
        return False
    tmp = path.with_suffix(".toml.tmp")
    tmp.write_text(text)
    tmp.replace(path)
    return True


def schedule_dump() -> None:
    """Rewrite the file once the current transaction commits, at most once per transaction."""
    if _suspended or not getattr(settings, "SEED_AUTOSAVE", True):
        return
    # One already queued here or in an enclosing savepoint will run (a rollback that drops it
    # drops this change too). One queued in a savepoint that has since ended might not.
    current = set(connection.savepoint_ids)
    for sids, func, _ in connection.run_on_commit:
        if func is _dump_after_commit and sids <= current:
            return
    transaction.on_commit(_dump_after_commit)


def _dump_after_commit() -> None:
    dump()


def on_change(sender, instance=None, update_fields=None, **kwargs) -> None:
    if update_fields and not set(update_fields) & SEEDED_FIELDS[sender]:
        return
    schedule_dump()
