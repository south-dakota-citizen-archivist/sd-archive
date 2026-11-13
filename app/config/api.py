"""REST API at /api/v1/ (docs at /api/v1/docs)."""

import json
from datetime import date, datetime

from django.contrib.gis.geos import Polygon
from django.shortcuts import get_object_or_404
from ninja import NinjaAPI, Schema
from ninja.pagination import LimitOffsetPagination, paginate

from census.models import Dataset, Source
from census.views import filtered_datasets
from places.models import Jurisdiction
from search import index

api = NinjaAPI(
    title="S.D. Archive",
    version="1",
    description="South Dakota public records and GIS data, with their sources",
)

MAX_FEATURES = 5000


class JurisdictionOut(Schema):
    slug: str
    kind: str
    geoid: str
    geoidfq: str
    ocd_id: str
    name: str
    valid_to: date | None


class SourceOut(Schema):
    slug: str
    name: str
    url: str
    platform: str
    last_crawled_at: datetime | None


class DatasetOut(Schema):
    id: int
    name: str
    url: str
    kind: str
    geometry_type: str
    feature_count: int | None
    status: str
    source: str
    jurisdiction: str | None
    topics: list[str]
    first_seen: datetime
    last_seen: datetime
    removed_at: datetime | None
    last_harvested_at: datetime | None

    @staticmethod
    def resolve_source(obj):
        return obj.source.slug

    @staticmethod
    def resolve_jurisdiction(obj):
        return obj.jurisdiction.slug if obj.jurisdiction else None

    @staticmethod
    def resolve_topics(obj):
        return [t.slug for t in obj.topics.all()]


class DatasetDetailOut(DatasetOut):
    description: str
    fields: list[dict]
    name_fields: list[str]
    object_id_field: str


class NameRecord(Schema):
    name: str
    field: str
    feature_key: str
    dataset_id: int
    dataset_name: str
    jurisdiction_name: str | None
    url: str


class NameMatch(Schema):
    name: str
    normalized: str
    n_records: int
    n_datasets: int
    score: float
    records: list[NameRecord]


@api.get("/jurisdictions", response=list[JurisdictionOut], tags=["census"])
def list_jurisdictions(request, kind: str | None = None, former: bool = False):
    """Places in force today, or with former=true, former ones too (Shannon County)."""
    qs = Jurisdiction.objects.defer("boundary")
    qs = qs if former else qs.current()
    return qs.filter(kind=kind) if kind else qs


@api.get("/sources", response=list[SourceOut], tags=["census"])
def list_sources(request):
    return Source.objects.all()


@api.get("/datasets", response=list[DatasetOut], tags=["census"])
@paginate(LimitOffsetPagination)
def list_datasets(
    request,
    q: str = "",
    place: str = "",
    topic: str = "",
    source: str = "",
    status: str = "",
    kind: str = "",
):
    qs, _ = filtered_datasets(
        {"q": q, "place": place, "topic": topic, "source": source, "status": status, "kind": kind}
    )
    return qs


@api.get("/datasets/{dataset_id}", response=DatasetDetailOut, tags=["census"])
def get_dataset(request, dataset_id: int):
    return get_object_or_404(
        Dataset.objects.select_related("source", "jurisdiction"), pk=dataset_id
    )


@api.get("/datasets/{dataset_id}/features", tags=["harvest"])
def dataset_features(
    request,
    dataset_id: int,
    bbox: str = "",
    limit: int = 1000,
    offset: int = 0,
):
    """Harvested features (as of the latest harvest) as GeoJSON. bbox is "minx,miny,maxx,maxy"
    (WGS84)."""
    dataset = get_object_or_404(Dataset, pk=dataset_id)
    qs = dataset.features
    if bbox:
        minx, miny, maxx, maxy = (float(v) for v in bbox.split(","))
        qs = qs.filter(geometry__bboverlaps=Polygon.from_bbox((minx, miny, maxx, maxy)))
    limit = max(1, min(limit, MAX_FEATURES))
    features = qs.order_by("key")[offset : offset + limit]
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "id": f.key,
                "properties": f.data,
                "geometry": json.loads(f.geometry.geojson) if f.geometry else None,
            }
            for f in features
        ],
    }


@api.get("/search/names", response=list[NameMatch], tags=["search"])
def search_names(request, q: str, limit: int = 50):
    """Best-matching names (fuzzy), each with up to 25 records it appears on."""
    return index.search(q, limit=max(1, min(limit, 200)))
