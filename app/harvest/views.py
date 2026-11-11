import json

from django.core.paginator import Paginator
from django.db import connection
from django.db.models import F, FloatField, TextField
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Cast, Length
from django.http import (
    FileResponse,
    Http404,
    HttpRequest,
    HttpResponse,
    JsonResponse,
    StreamingHttpResponse,
)
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.utils.text import slugify

from census.models import Dataset
from harvest import export
from harvest.labels import Labels
from places.models import Jurisdiction
from records.models import Record, Run
from sdgis.arcgis import client

NUMERIC_TYPES = {
    "esriFieldTypeOID",
    "esriFieldTypeInteger",
    "esriFieldTypeSmallInteger",
    "esriFieldTypeBigInteger",
    "esriFieldTypeDouble",
    "esriFieldTypeSingle",
}
HIDDEN_FIELD_TYPES = {"esriFieldTypeGeometry"}
PAGE_SIZE = 50


def get_dataset(pk: int) -> Dataset:
    return get_object_or_404(
        Dataset.objects.select_related("source__publisher", "jurisdiction"), pk=pk
    )


def columns(dataset: Dataset) -> list[dict]:
    return [f for f in dataset.fields if f.get("type") not in HIDDEN_FIELD_TYPES]


def tab_context(dataset: Dataset, tab: str) -> dict:
    return {
        "dataset": dataset,
        "tab": tab,
        "harvested": dataset.harvests.filter(status="succeeded").exists(),
    }


def dataset_table(request: HttpRequest, pk: int) -> HttpResponse:
    dataset = get_dataset(pk)
    context = tab_context(dataset, "table") | {"columns": columns(dataset)}
    if not context["harvested"]:
        return render(request, "harvest/dataset_table.html", context)

    q = request.GET.get("q", "").strip()
    sort = request.GET.get("sort", "")
    desc = request.GET.get("dir") == "desc"
    features = dataset.features.only("key", "data")
    if q:
        features = features.annotate(props_text=Cast("data", TextField())).filter(
            props_text__icontains=q
        )
    field_types = {f["name"]: f.get("type") for f in dataset.fields}
    if sort in field_types:
        value = KeyTextTransform(sort, "data")
        if field_types[sort] in NUMERIC_TYPES:
            value = Cast(value, FloatField())
        features = features.annotate(sort_value=value).order_by(
            F("sort_value").desc(nulls_last=True) if desc else F("sort_value").asc(nulls_last=True)
        )
    else:
        # Numeric IDs sort as numbers: "2" before "10"
        features = features.order_by(Length("key"), "key")

    page = Paginator(features, PAGE_SIZE).get_page(request.GET.get("page"))
    names = [c["name"] for c in context["columns"]]
    labels = Labels(dataset.fields)
    context |= {
        "page": page,
        "rows": [(f.key, [labels.cell(n, f.data) for n in names]) for f in page],
        "q": q,
        "sort": sort,
        "dir": "desc" if desc else "asc",
    }
    template = (
        "harvest/_table_rows.html"
        if request.headers.get("HX-Request")
        else "harvest/dataset_table.html"
    )
    return render(request, template, context)


def dataset_preview_table(request: HttpRequest, pk: int) -> HttpResponse:
    """First rows straight from the source, loaded lazily for datasets not yet harvested."""
    dataset = get_dataset(pk)
    try:
        df = client.preview_features(client.make_session(retries=1), dataset.url, limit=100)
        error = ""
    except Exception as e:
        df, error = None, f"{type(e).__name__}: {e}"
    names = [c["name"] for c in columns(dataset)]
    rows = []
    if df is not None:
        records = json.loads(
            df.drop(columns="geometry", errors="ignore").to_json(
                orient="records", date_format="iso"
            )
        )
        labels = Labels(dataset.fields)
        rows = [[labels.cell(n, r) for n in names] for r in records]
    return render(
        request,
        "harvest/_preview_rows.html",
        {"columns": columns(dataset), "rows": rows, "error": error},
    )


def dataset_map(request: HttpRequest, pk: int) -> HttpResponse:
    dataset = get_dataset(pk)
    context = tab_context(dataset, "map")
    extent = None
    if context["harvested"]:
        with connection.cursor() as cur:
            cur.execute(
                """
                SELECT ST_XMin(e), ST_YMin(e), ST_XMax(e), ST_YMax(e)
                FROM (
                    SELECT ST_Extent(f.geometry) AS e
                    FROM records_record f
                    JOIN records_recordset rs ON rs.id = f.recordset_id
                    WHERE rs.gis_dataset_id = %s AND f.dropped_at IS NULL
                ) x
                """,
                [dataset.pk],
            )
            row = cur.fetchone()
            extent = list(row) if row and row[0] is not None else None
    context["extent"] = json.dumps(extent)
    return render(request, "harvest/dataset_map.html", context)


def dataset_changes(request: HttpRequest, pk: int) -> HttpResponse:
    dataset = get_dataset(pk)
    harvests = Paginator(dataset.harvests.all(), 25).get_page(request.GET.get("page"))
    return render(
        request,
        "harvest/dataset_changes.html",
        tab_context(dataset, "changes") | {"harvests": harvests},
    )


def dataset_download(request: HttpRequest, pk: int) -> HttpResponse:
    dataset = get_dataset(pk)
    latest = dataset.harvests.filter(status="succeeded").exclude(export="").first()
    return render(
        request,
        "harvest/dataset_download.html",
        tab_context(dataset, "download")
        | {"latest_export": latest, "download_slug": download_slug(dataset)},
    )


def download_slug(dataset: Dataset) -> str:
    """e.g. 2026-11-01-lawrence-county-sd-parcels: when last harvested, where, what."""
    place = dataset.jurisdiction
    if place is None:
        where = dataset.source.slug
    elif place.kind == Jurisdiction.Kind.STATE:
        where = place.slug
    else:
        where = f"{place.slug}-sd"
    what = slugify(dataset.name) or f"dataset-{dataset.pk}"
    if place:
        what = what.removeprefix(f"{place.slug}-")  # "Lawrence County Parcels"
    when = timezone.localdate(dataset.last_harvested_at) if dataset.last_harvested_at else None
    return "-".join(str(p) for p in (when, where, what) if p)


def download(request: HttpRequest, pk: int, fmt: str) -> HttpResponse:
    dataset = get_dataset(pk)
    slug = download_slug(dataset)
    if fmt == "parquet":
        latest = dataset.harvests.filter(status="succeeded").exclude(export="").first()
        if not latest:
            raise Http404("no export yet")
        return FileResponse(
            latest.export.open("rb"), as_attachment=True, filename=f"{slug}.parquet"
        )
    if fmt == "geojson":
        response = StreamingHttpResponse(
            export.stream_geojson(dataset.pk), content_type="application/geo+json"
        )
    elif fmt == "csv":
        response = StreamingHttpResponse(
            export.stream_csv(
                dataset.pk, [c["name"] for c in columns(dataset)], Labels(dataset.fields)
            ),
            content_type="text/csv",
        )
    else:
        raise Http404
    response["Content-Disposition"] = f'attachment; filename="{slug}.{fmt}"'
    return response


def feature_detail(request: HttpRequest, pk: int, key: str) -> HttpResponse:
    dataset = get_dataset(pk)
    feature = (  # a dropped feature keeps its page
        Record.objects.filter(recordset__gis_dataset=dataset, key=key).select_related("run").first()
    )
    if feature is None:
        raise Http404("no such feature (it may have left the source)")
    labels = Labels(dataset.fields)
    names = [c["name"] for c in columns(dataset)] or list(feature.data)
    return render(
        request,
        "harvest/feature_detail.html",
        {
            "dataset": dataset,
            "key": key,
            "feature": feature,
            "attributes": [(n, *labels.cell(n, feature.data)) for n in names],
            "geometry": feature.geometry.geojson if feature.geometry else "null",
        },
    )


def feature_json(request: HttpRequest, pk: int, key: str) -> JsonResponse:
    feature = (
        Record.objects.filter(recordset__gis_dataset_id=pk, key=key).only("key", "data").first()
    )
    if not feature:
        raise Http404
    return JsonResponse({"key": feature.key, "properties": feature.data})


def harvest_detail(request: HttpRequest, pk: int) -> HttpResponse:
    harvest = get_object_or_404(
        Run.objects.select_related("recordset__gis_dataset__jurisdiction"),
        pk=pk,
        recordset__gis_dataset__isnull=False,
    )
    first_harvest = not harvest.dataset.harvests.filter(
        status=Run.Status.SUCCEEDED, started_at__lt=harvest.started_at
    ).exists()
    # Records this harvest last wrote, as they are now (a later harvest may have rewritten
    # some). A first harvest adds everything; no need to list every record to say so.
    written = (
        []
        if first_harvest
        else list(harvest.records.order_by("key").only("key", "data", "first_seen"))
    )
    added = [f for f in written if f.first_seen == harvest.started_at]
    changed = [f for f in written if f.first_seen != harvest.started_at]
    # Records this harvest found gone and that haven't come back since
    dropped = list(
        harvest.recordset.records.filter(dropped_at=harvest.started_at)
        .order_by("key")
        .only("key", "data")
    )
    labels = Labels(harvest.dataset.fields)
    limit = 500
    fields = label_fields(harvest.dataset)

    def labelled(features):
        return [
            (
                f.key,
                " · ".join(str(labels.cell(n, f.data)[0]) for n in fields if f.data.get(n)),
            )
            for f in features[:limit]
        ]

    return render(
        request,
        "harvest/harvest_detail.html",
        {
            "harvest": harvest,
            "dataset": harvest.dataset,
            "sections": [
                ("Changed records", labelled(changed), harvest.changed),
                ("Added records", labelled(added), harvest.added),
                ("Dropped records (gone from the source)", labelled(dropped), len(dropped)),
            ],
            "limit": limit,
            "first_harvest": first_harvest,
        },
    )


def label_fields(dataset: Dataset) -> list[str]:
    """A few fields that identify a record at a glance: names first, then IDs/addresses."""
    picks = list(dataset.name_fields[:2])
    for f in dataset.fields:
        n = f["name"]
        if len(picks) >= 4:
            break
        if n not in picks and any(
            w in n.upper() for w in ("PARCEL", "PIN", "ADDR", "SITUS", "NAME")
        ):
            picks.append(n)
    return picks


TILE_SQL = """
WITH bounds AS (SELECT ST_TileEnvelope(%(z)s, %(x)s, %(y)s) AS geom),
mvtgeom AS (
    SELECT ST_AsMVTGeom(ST_Transform(f.geometry, 3857), bounds.geom, 4096, 64, true) AS geom,
           f.key
    FROM records_record f JOIN records_recordset rs ON rs.id = f.recordset_id, bounds
    WHERE rs.gis_dataset_id = %(dataset_id)s AND f.dropped_at IS NULL
      AND f.geometry && ST_Transform(bounds.geom, 4326)
)
SELECT ST_AsMVT(mvtgeom.*, 'features') FROM mvtgeom
"""


def tile(request: HttpRequest, pk: int, z: int, x: int, y: int) -> HttpResponse:
    with connection.cursor() as cur:
        cur.execute(TILE_SQL, {"z": z, "x": x, "y": y, "dataset_id": pk})
        data = cur.fetchone()[0]
    response = HttpResponse(bytes(data or b""), content_type="application/vnd.mapbox-vector-tile")
    response["Cache-Control"] = "max-age=300"
    return response
