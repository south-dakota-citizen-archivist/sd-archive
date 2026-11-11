"""Turn a layer's download into items for its record set (records.ingest stores them).

Each feature is keyed by its object ID (or, without one, by its content hash).
"""

import hashlib
import json

import geopandas as gpd

from records import ingest
from records.models import Run
from records.sources import Item


def prepare(df: gpd.GeoDataFrame, oid_field: str | None) -> list[Item]:
    """Turn a download into JSON-safe items with stable keys and content hashes."""
    has_geometry = isinstance(df, gpd.GeoDataFrame) and df._geometry_column_name in df.columns
    attrs = df.drop(columns=df.geometry.name) if has_geometry else df
    # to_json handles NaN → null, numpy scalars and timestamps (ISO 8601) consistently
    records = json.loads(attrs.to_json(orient="records", date_format="iso"))
    wkbs = list(df.geometry.to_wkb()) if has_geometry else [None] * len(records)

    items = []
    for props, wkb in zip(records, wkbs, strict=True):
        digest = hashlib.sha256(json.dumps(props, sort_keys=True, separators=(",", ":")).encode())
        if wkb is not None:
            digest.update(wkb)
        content_hash = digest.hexdigest()
        oid = props.get(oid_field) if oid_field else None
        key = str(oid) if oid is not None else content_hash
        items.append(Item(key=key, data=props, geometry=wkb, content_hash=content_hash))
    return items


def dataset_hash(items: list[Item]) -> str:
    """One hash for the whole download, independent of row order."""
    return ingest.run_hash(items)


def current_count(dataset) -> int:
    return dataset.features.count()


def apply(dataset, harvest: Run, items: list[Item]) -> dict[str, int]:
    """Store a download as the layer's records; harvest is the run doing it."""
    return ingest.store(harvest, items).counts()
