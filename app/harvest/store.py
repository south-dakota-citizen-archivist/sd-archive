"""Write a download into the layer's records (records.Record): the latest version of each only.

Each record is keyed by its object ID (or, without one, by its content hash). For each key,
an unchanged record is left alone, a changed one is overwritten, a new one is inserted, and
one missing from the download is deleted.
"""

import hashlib
import json
from dataclasses import dataclass
from itertools import batched

import geopandas as gpd
from django.contrib.gis.geos import GEOSGeometry
from django.db import transaction

from records.gis import recordset_for
from records.models import Record, Run

BATCH = 2000


@dataclass
class Row:
    key: str
    properties: dict
    wkb: bytes | None
    content_hash: str


def prepare(df: gpd.GeoDataFrame, oid_field: str | None) -> list[Row]:
    """Turn a download into JSON-safe rows with stable keys and content hashes."""
    has_geometry = isinstance(df, gpd.GeoDataFrame) and df._geometry_column_name in df.columns
    attrs = df.drop(columns=df.geometry.name) if has_geometry else df
    # to_json handles NaN → null, numpy scalars and timestamps (ISO 8601) consistently
    records = json.loads(attrs.to_json(orient="records", date_format="iso"))
    wkbs = list(df.geometry.to_wkb()) if has_geometry else [None] * len(records)

    rows = []
    for props, wkb in zip(records, wkbs, strict=True):
        digest = hashlib.sha256(json.dumps(props, sort_keys=True, separators=(",", ":")).encode())
        if wkb is not None:
            digest.update(wkb)
        content_hash = digest.hexdigest()
        oid = props.get(oid_field) if oid_field else None
        key = str(oid) if oid is not None else content_hash
        rows.append(Row(key=key, properties=props, wkb=wkb, content_hash=content_hash))
    return rows


def dataset_hash(rows: list[Row]) -> str:
    """One hash for the whole download, independent of row order."""
    digest = hashlib.sha256()
    for h in sorted(r.content_hash for r in rows):
        digest.update(h.encode())
    return digest.hexdigest()


def current_count(dataset) -> int:
    return dataset.features.count()


@transaction.atomic
def apply(dataset, harvest: Run, rows: list[Row]) -> dict[str, int]:
    """Store a download as the layer's records; harvest is the run doing it."""
    recordset = recordset_for(dataset)
    now = harvest.started_at
    incoming = {r.key: r for r in rows}  # identical rows without IDs collapse to one
    current = {
        key: (pk, content_hash)
        for pk, key, content_hash in Record.objects.filter(recordset=recordset).values_list(
            "pk", "key", "content_hash"
        )
    }

    added = [k for k in incoming if k not in current]
    changed = [k for k in incoming if k in current and current[k][1] != incoming[k].content_hash]
    removed = [k for k in current if k not in incoming]

    for chunk in batched([current[k][0] for k in removed], BATCH):
        Record.objects.filter(pk__in=chunk).delete()

    def geometry(r: Row):
        return GEOSGeometry(memoryview(r.wkb), srid=4326) if r.wkb else None

    updates = []
    for key in changed:
        r = incoming[key]
        updates.append(Record(pk=current[key][0], data=r.properties, geometry=geometry(r),
                              content_hash=r.content_hash, run=harvest))  # fmt: skip
    Record.objects.bulk_update(
        updates, ["data", "geometry", "content_hash", "run"], batch_size=BATCH
    )
    Record.objects.bulk_create(
        [
            Record(
                recordset=recordset,
                key=key,
                data=incoming[key].properties,
                geometry=geometry(incoming[key]),
                content_hash=incoming[key].content_hash,
                first_seen=now,
                run=harvest,
            )  # fmt: skip
            for key in added
        ],
        batch_size=BATCH,
    )

    return {
        "added": len(added),
        "changed": len(changed),
        "removed": len(removed),
        "unchanged": len(incoming) - len(added) - len(changed),
    }
