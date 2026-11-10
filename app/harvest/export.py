"""Bulk download files: the latest GeoParquet file per dataset, plus CSV/GeoJSON on demand."""

import csv
import io
import json
from collections.abc import Iterator
from pathlib import Path

import geopandas as gpd
from django.conf import settings
from django.db import connection

from harvest.labels import Labels
from harvest.models import Harvest


def export_relpath(harvest: Harvest) -> str:
    """Where a harvest writes its file while it runs."""
    return f"exports/{harvest.dataset_id}/harvest-{harvest.pk}.parquet"


def latest_relpath(dataset_id: int) -> str:
    return f"exports/{dataset_id}/latest.parquet"


def write_parquet(df: gpd.GeoDataFrame, harvest: Harvest) -> str:
    """Write this harvest's file via a temp file and rename, so a failed write leaves nothing
    behind. promote() makes it the dataset's file once the harvest is stored."""
    relpath = export_relpath(harvest)
    path = Path(settings.MEDIA_ROOT) / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        if isinstance(df, gpd.GeoDataFrame) and df._geometry_column_name in df.columns:
            df.to_parquet(tmp, compression="zstd", index=False)
        else:
            df.to_parquet(tmp, compression="zstd", index=False, engine="pyarrow")
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)
    return relpath


def promote(harvest: Harvest) -> None:
    """After a harvest is stored: its file becomes the dataset's only one (latest.parquet),
    and no earlier harvest points at a file."""
    if not harvest.export:
        return
    media = Path(settings.MEDIA_ROOT)
    latest = latest_relpath(harvest.dataset_id)
    (media / harvest.export.name).replace(media / latest)
    Harvest.objects.filter(pk=harvest.pk).update(export=latest)
    harvest.export.name = latest
    Harvest.objects.filter(dataset_id=harvest.dataset_id).exclude(pk=harvest.pk).update(export="")


def iter_current(dataset_id: int, chunk: int = 2000) -> Iterator[tuple[str, dict, str | None]]:
    """(key, properties, geometry as GeoJSON text) for current features, streamed."""
    with connection.cursor() as cur:
        cur.execute(
            """
            SELECT key, properties, ST_AsGeoJSON(geometry, 7)
            FROM harvest_feature
            WHERE dataset_id = %s
            ORDER BY length(key), key
            """,
            [dataset_id],
        )
        while rows := cur.fetchmany(chunk):
            for key, props, geom in rows:
                yield key, props if isinstance(props, dict) else json.loads(props), geom


def stream_geojson(dataset_id: int) -> Iterator[str]:
    yield '{"type":"FeatureCollection","features":[\n'
    first = True
    for key, props, geom in iter_current(dataset_id):
        feature = (
            f'{{"type":"Feature","id":{json.dumps(key)},'
            f'"properties":{json.dumps(props)},"geometry":{geom or "null"}}}'
        )
        yield ("" if first else ",\n") + feature
        first = False
    yield "\n]}\n"


def stream_csv(dataset_id: int, columns: list[str], labels: Labels | None = None) -> Iterator[str]:
    """Raw values, plus a FIELD_label column after each coded field."""
    coded = labels.coded_fields if labels else set()
    header = ["_key"]
    for c in columns:
        header += [c, f"{c}_label"] if c in coded else [c]
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([*header, "_geometry_geojson"])
    yield buf.getvalue()
    for key, props, geom in iter_current(dataset_id):
        row = [key]
        for c in columns:
            row.append(props.get(c))
            if c in coded:
                row.append(labels.label(c, props) or "")
        buf.seek(0)
        buf.truncate()
        writer.writerow([*row, geom or ""])
        yield buf.getvalue()
