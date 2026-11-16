"""What the legislature record sets are built from: the crawl's stored API responses (no
fetching), the legislature data repository's files (1997–2024, in the raw store), and the
records already stored.

**Values the source has stopped giving are kept.** The API still lists legislators' home
addresses, phones and email, but leaves them empty now; earlier copies had them. When a
field is empty in the API but an earlier copy had a value (the repository, a record imported
from the earlier system, or this record set's last run), the value goes in the record's
`earlier`: `{field: {"value": …, "from": where it was found}}`. A value the API gives always
wins, and nothing in `earlier` is dropped.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from functools import cache
from pathlib import Path

from .crawl import raw_root, store_name

STATE = "0400000US46"
SITE = "https://sdlegislature.gov/"


def api(path: str):
    """The stored response for an API path (None when the crawl has none, or it's empty)."""
    p = raw_root() / store_name(path)
    if not p.exists():
        return None
    body = p.read_bytes()
    if not body.strip():
        return None
    try:
        return json.loads(body)
    except ValueError:
        return None


def api_url(path: str) -> str:
    return f"{SITE}api/{path}"


def archive_root() -> Path:
    from django.conf import settings

    return Path(settings.MEDIA_ROOT) / "raw" / "legislature-archive"


@cache
def _archive_urls() -> dict[str, str]:
    manifest = archive_root() / "manifest.jsonl"
    if not manifest.exists():
        return {}
    out = {}
    for line in manifest.read_text().splitlines():
        if line.strip():
            row = json.loads(line)
            out[row["path"]] = row["url"]
    return out


def archive(name: str):
    """A file of the legislature data repository (data/…, crawler/…), parsed: JSON, or CSV
    as a list of dicts. None if it isn't there."""
    p = archive_root() / name
    if not p.exists():
        return None
    if name.endswith(".csv"):
        import csv

        with p.open(newline="") as f:
            return list(csv.DictReader(f))
    return json.loads(p.read_bytes())


def archive_url(name: str) -> str:
    """Where a repository file is published (on GitHub, at the commit it was copied from)."""
    return _archive_urls().get(name, name)


CROSSWALK = "crawler/sd-legislator-xwalk.csv"


@cache
def crosswalk() -> dict[int, int]:
    """Session profile (SessionMemberId) → legislator (the Historical Listing's MemberId),
    checked by hand, 1997–2024 (the repository's crawler/sd-legislator-xwalk.csv)."""
    rows = archive(CROSSWALK) or []
    return {int(r["legislator_profile_id"]): int(r["legislator_id_canon"]) for r in rows
            if r["legislator_id_canon"].strip()}  # fmt: skip


def empty(value) -> bool:
    return value is None or value == "" or value == [] or value == "None"


def iso_day(day):
    """A date as the Historical Listing writes it (7-03-1850, 02/07/1916) as ISO; anything
    else (a year alone, "-1830", a partial date) as it is, spaces collapsed."""
    m = re.fullmatch(r"(\d{1,2})[-/](\d{1,2})[-/](\d{4})", str(day or "").strip())
    if m:
        return f"{m[3]}-{int(m[1]):02d}-{int(m[2]):02d}"
    return None if empty(day) else " ".join(str(day).split())


def keep_earlier(data: dict, fields: Iterable[str], *copies: tuple[dict | None, str],
                 carried: dict | None = None) -> dict:  # fmt: skip
    """data with `earlier` filled in, for each field empty in data: what `carried` (the
    stored record's `earlier`) already had, else the first value an earlier copy has
    (copies: (that copy's values by field, where it's from)). Returns data."""
    earlier = {k: v for k, v in (carried or {}).items() if empty(data.get(k))}
    for field in fields:
        if not empty(data.get(field)) or field in earlier:
            continue
        for values, source in copies:
            if values and not empty(values.get(field)):
                earlier[field] = {"value": values[field], "from": source}
                break
    if earlier:
        data["earlier"] = dict(sorted(earlier.items()))
    else:
        data.pop("earlier", None)
    return data


def keep_changed(data: dict, fields: Iterable[str], copy: tuple[dict | None, str]) -> dict:
    """data with `earlier` holding, for each field the API now gives differently from an
    earlier copy, that copy's value: for a past session, the API shows some things as they
    are now (a legislator's current occupation and photo, a committee's current meeting
    days), where the earlier copy has them as they were listed then. data keeps the API's
    value. Returns data."""
    values, source = copy
    for field in fields:
        theirs, ours = (values or {}).get(field), data.get(field)
        if not empty(theirs) and not empty(ours) and _same(theirs) != _same(ours):
            data.setdefault("earlier", {})[field] = {"value": theirs, "from": source}
    if data.get("earlier"):
        data["earlier"] = dict(sorted(data["earlier"].items()))
    return data


def _same(value) -> str:
    return " ".join(str(value).replace("\xa0", " ").split())


def stored(slug: str) -> dict:
    """The record set's current records, by key."""
    from records.models import Record

    return {r.key: r for r in Record.objects.filter(recordset__slug=slug, dropped_at__isnull=True)}


def last_run(record) -> tuple[dict | None, str]:
    """A stored record's own values as an earlier copy (for keep_earlier)."""
    if record is None:
        return None, ""
    return record.data, f"{SITE}api/ (as of {record.updated_at:%Y-%m-%d})"
