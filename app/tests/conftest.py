import json
import re

import pytest

from platforms.arcgis import client

LAYER_URL = "https://example.com/arcgis/rest/services/Test/FeatureServer/0"
# Gaps, like a real layer after deletions
IDS = [1, 2, 3, 5, 8, 13, 21, 34, 55, 89]
RANGE_RE = re.compile(r"OBJECTID >= (\d+) AND OBJECTID <= (\d+)")


class FakeResponse:
    def __init__(self, payload: dict, status: int = 200):
        self.payload = payload
        self.status_code = status
        self.content = json.dumps(payload).encode()

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return self.payload


class FakeServer:
    """A tiny ArcGIS layer at LAYER_URL that stands in for requests.Session.

    - max_record_count: the limit advertised in metadata
    - hidden_limit: a lower limit actually enforced (some servers do this)
    - pagination: whether resultOffset is honored
    - has_oid: whether the layer exposes an object ID field
    - reported_count: override returnCountOnly, to simulate a mismatch
    - names / owners: per-ID attribute overrides, to simulate edits between harvests
    - moved: IDs whose point geometry shifts
    """

    def __init__(
        self,
        ids: list[int] | None = None,
        max_record_count: int = 4,
        hidden_limit: int | None = None,
        pagination: bool = True,
        has_oid: bool = True,
        reported_count: int | None = None,
    ):
        self.ids = sorted(IDS if ids is None else ids)
        self.max_record_count = max_record_count
        self.limit = hidden_limit or max_record_count
        self.pagination = pagination
        self.has_oid = has_oid
        self.reported_count = reported_count
        self.names: dict[int, str] = {}
        self.owners: dict[int, str] = {}
        self.moved: set[int] = set()
        self.extra_metadata: dict = {}  # merged into layer metadata, e.g. editingInfo
        self.calls: list[tuple[str, dict]] = []

    @property
    def fields(self) -> list[dict]:
        oid_type = "esriFieldTypeOID" if self.has_oid else "esriFieldTypeInteger"
        return [
            {"name": "OBJECTID", "type": oid_type, "alias": "OBJECTID"},
            {"name": "NAME", "type": "esriFieldTypeString", "alias": "NAME", "length": 50},
            {"name": "OWNER_NAM", "type": "esriFieldTypeString", "alias": "Owner", "length": 80},
        ]

    @property
    def metadata(self) -> dict:
        meta = {
            "name": "Towns",
            "type": "Feature Layer",
            "geometryType": "esriGeometryPoint",
            "maxRecordCount": self.max_record_count,
            "advancedQueryCapabilities": {"supportsPagination": self.pagination},
            "fields": self.fields,
        }
        if self.has_oid:
            meta["objectIdField"] = "OBJECTID"
        return meta | self.extra_metadata

    def feature(self, oid: int) -> dict:
        shift = 0.5 if oid in self.moved else 0
        return {
            "attributes": {
                "OBJECTID": oid,
                "NAME": self.names.get(oid, f"Town {oid}"),
                "OWNER_NAM": self.owners.get(oid, f"SMITH, JOHN {oid}"),
            },
            "geometry": {"x": -103 - oid / 100 + shift, "y": 44 + oid / 100},
        }

    def get(self, url: str, params: dict | None = None, timeout: float | None = None):
        params = params or {}
        self.calls.append((url, params))

        if not url.startswith(LAYER_URL):
            return FakeResponse({"error": {"code": 404, "message": "Not found", "details": []}})
        if not url.endswith("/query"):
            return FakeResponse(self.metadata)

        ids = self.ids
        if m := RANGE_RE.search(params["where"]):
            lo, hi = int(m[1]), int(m[2])
            ids = [i for i in ids if lo <= i <= hi]

        if params.get("returnCountOnly") == "true":
            count = len(ids) if self.reported_count is None else self.reported_count
            return FakeResponse({"count": count})
        if params.get("returnIdsOnly") == "true":
            # Real servers don't promise any order
            return FakeResponse({"objectIdFieldName": "OBJECTID", "objectIds": ids[::-1]})

        offset = int(params.get("resultOffset", 0)) if self.pagination else 0
        limit = min(int(params.get("resultRecordCount", self.limit)), self.limit)
        page = ids[offset : offset + limit]
        return FakeResponse(
            {
                "geometryType": "esriGeometryPoint",
                "spatialReference": {"wkid": 4326, "latestWkid": 4326},
                "fields": self.fields,
                "features": [self.feature(i) for i in page],
                "exceededTransferLimit": len(ids) - offset > limit,
            }
        )

    def query_calls(self) -> list[dict]:
        """Feature queries (not count/ID queries)."""
        return [
            p
            for u, p in self.calls
            if u.endswith("/query") and "returnCountOnly" not in p and "returnIdsOnly" not in p
        ]


@pytest.fixture
def server(monkeypatch) -> FakeServer:
    """Route all ArcGIS HTTP to a FakeServer. Tests can reassign attributes on it."""
    fake = FakeServer()
    monkeypatch.setattr(client, "make_session", lambda *a, **kw: fake)
    return fake


@pytest.fixture(autouse=True)
def media_root(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"
    return settings.MEDIA_ROOT


@pytest.fixture(autouse=True)
def seed_file(monkeypatch, tmp_path):
    """Never let a test rewrite the real seeds/sources.toml."""
    from census import seed

    path = tmp_path / "sources.toml"
    monkeypatch.setattr(seed, "SEED_FILE", path)
    return path


# --- Database fixtures ---------------------------------------------------------------

SQUARE = "MULTIPOLYGON(((-104 44, -103 44, -103 45, -104 45, -104 44)))"


@pytest.fixture
def state(db):
    from census.models import Jurisdiction

    return Jurisdiction.objects.create(
        kind="state",
        geoid="46",
        name="South Dakota",
        short_name="South Dakota",
        slug="south-dakota",
    )


@pytest.fixture
def county(state):
    from django.contrib.gis.geos import GEOSGeometry

    from census.models import Jurisdiction

    return Jurisdiction.objects.create(
        kind="county",
        geoid="46999",
        name="Test County",
        short_name="Test",
        slug="test-county",
        parent=state,
        boundary=GEOSGeometry(SQUARE, srid=4326),
    )


@pytest.fixture
def source(county):
    from census.models import Publisher, Source

    publisher = Publisher.objects.create(name="Test County GIS", jurisdiction=county)
    return Source.objects.create(
        name="Test server",
        slug="test-server",
        url="https://example.com/arcgis/rest/services",
        platform="arcgis_server",
        publisher=publisher,
        jurisdiction=county,
    )


@pytest.fixture
def dataset(source, server):
    """A watched dataset backed by the FakeServer at LAYER_URL."""
    from django.utils import timezone

    from census import classify
    from census.models import Dataset

    now = timezone.now()
    fields = [{"name": f["name"], "type": f["type"], "alias": f["alias"]} for f in server.fields]
    dataset = Dataset.objects.create(
        source=source,
        url=LAYER_URL,
        layer_id=0,
        name="Towns",
        kind="layer",
        geometry_type="esriGeometryPoint",
        fields=fields,
        fields_hash=classify.fields_hash(fields),
        object_id_field="OBJECTID",
        feature_count=len(server.ids),
        jurisdiction=source.jurisdiction,
        status=Dataset.Status.WATCHED,
        name_fields=classify.suggest_name_fields(fields),
        first_seen=now,
        last_seen=now,
    )
    dataset.search_text = classify.search_text(dataset)
    dataset.save(update_fields=["search_text"])
    return dataset
