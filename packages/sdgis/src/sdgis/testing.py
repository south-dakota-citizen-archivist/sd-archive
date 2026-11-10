"""Fake ArcGIS servers for tests, in any project that uses sdgis.

Both stand in for requests.Session: patch ``sdgis.arcgis.client.make_session`` to return one.

- FakeServer: one feature layer at LAYER_URL (paging, hidden record limits, missing object IDs,
  count mismatches, edits between harvests)
- FakeDirectory: a services directory at ROOT (folders, MapServer/FeatureServer duplicates,
  a stopped service, tables, counts)

    @pytest.fixture
    def server(monkeypatch):
        fake = FakeServer()
        monkeypatch.setattr(client, "make_session", lambda *a, **kw: fake)
        return fake
"""

import json
import re

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


# --- A services directory ---------------------------------------------------

ROOT = "https://gis.example.com/arcgis/rest/services"


def field(name: str, type_: str = "esriFieldTypeString", alias: str | None = None) -> dict:
    return {"name": name, "type": type_, "alias": alias or name}


OID = field("OBJECTID", "esriFieldTypeOID")
PARCEL_FIELDS = [
    OID,
    field("PIN"),
    field("OWNER", alias="Owner"),
    field("SALE_PR", "esriFieldTypeDouble"),
]
# MapServer also lists geometry and shape metrics; FeatureServer doesn't
MAPSERVER_EXTRAS = [
    field("Shape", "esriFieldTypeGeometry"),
    field("Shape.STArea()", "esriFieldTypeDouble"),
]
PARCELS = {
    "id": 0,
    "name": "Parcels",
    "description": "Tax parcels",
    "type": "Feature Layer",
    "geometryType": "esriGeometryPolygon",
    "maxRecordCount": 2000,
    "capabilities": "Map,Query,Data",
    "advancedQueryCapabilities": {"supportsPagination": True},
    "fields": PARCEL_FIELDS,
}
TAX_TABLE = {
    "id": 1,
    "name": "Tax History",
    "type": "Table",
    "capabilities": "Query",
    "fields": [OID, field("PIN"), field("YEAR", "esriFieldTypeInteger")],
}

ROUTES = {
    ROOT: {
        "folders": ["County"],
        "services": [
            {"name": "Basemap", "type": "MapServer"},
            {"name": "Geocoder", "type": "GeocodeServer"},
        ],
    },
    f"{ROOT}/County": {
        "folders": [],
        "services": [
            {"name": "County/parcels", "type": "MapServer"},
            {"name": "County/parcels", "type": "FeatureServer"},
            {"name": "County/old", "type": "MapServer"},
        ],
    },
    f"{ROOT}/Basemap/MapServer/layers": {
        "layers": [
            {"id": 0, "name": "Labels", "type": "Group Layer"},
            {"id": 1, "name": "Roads", "type": "Feature Layer", "fields": [OID, field("NAME")]},
            {"id": 2, "name": "Imagery", "type": "Raster Layer"},
            {
                "id": 3,
                "name": "Hidden",
                "type": "Feature Layer",
                "capabilities": "Map",
                "fields": [],
            },
        ],
        "tables": [],
    },
    f"{ROOT}/County/parcels/MapServer/layers": {
        "layers": [{**PARCELS, "fields": PARCEL_FIELDS + MAPSERVER_EXTRAS}],
        "tables": [TAX_TABLE],
    },
    f"{ROOT}/County/parcels/FeatureServer/layers": {"layers": [PARCELS], "tables": [TAX_TABLE]},
}
COUNTS = {
    f"{ROOT}/Basemap/MapServer/1": 120,
    f"{ROOT}/County/parcels/FeatureServer/0": 5000,
    f"{ROOT}/County/parcels/FeatureServer/1": 25000,
}


class FakeDirectory:
    def __init__(self, routes=None):
        self.routes = dict(routes or ROUTES)
        self.calls: list[tuple[str, float | None]] = []

    def get(self, url: str, params: dict | None = None, timeout: float | None = None):
        self.calls.append((url, timeout))
        if url.endswith("/query") and (params or {}).get("returnCountOnly") == "true":
            return FakeResponse({"count": COUNTS[url.removesuffix("/query")]})
        if url in self.routes:
            return FakeResponse(self.routes[url])
        message = "Service County/old/MapServer not started" if "/old/" in url else "Not found"
        return FakeResponse({"error": {"code": 500, "message": message, "details": []}})


# --- Schneider Beacon's client picker (saved from beacon.schneidercorp.com) ---

PICKER = """
<div id="areaMenuContent" role="listbox">
<div class="state-group" role="group">
  <div id="mru-group" class="state-group-label faves">Recently used</div>
  <div id="mru1225TestCountySD" class="faves" data-appid="1225">Test County,  SD</div>
</div>
<div class="state-group" data-state="Alabama" role="group">
  <div id="Alabama-label" class="state-group-label">Alabama</div>
  <div id="client334CityofFoleyAL" class="dropdown-option" data-appid="334">City of Foley, AL</div>
</div>
<div class="state-group" data-state="South Dakota" role="group">
  <div id="SouthDakota-label" class="state-group-label">South Dakota</div>
  <div id="client1225TestCountySD" class="dropdown-option" data-appid="1225">Test County,  SD</div>
  <div id="client95McCookCountySD" class="dropdown-option" data-appid="95">McCook County, SD</div>
</div>
</div>
"""
