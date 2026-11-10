import pytest

from sdgis.arcgis import client
from tests.conftest import IDS, LAYER_URL, FakeResponse, FakeServer


def fetch(server: FakeServer, where: str = "1=1", batch_size: int | None = None):
    return client.fetch_features(server, LAYER_URL, server.metadata, where, batch_size)


def test_fetch_by_id_ranges():
    server = FakeServer()
    gdf = fetch(server)

    assert sorted(gdf["OBJECTID"]) == IDS
    assert gdf.crs.to_epsg() == 4326
    # 10 IDs in batches of maxRecordCount=4
    assert [p["where"] for p in server.query_calls()] == [
        "(1=1) AND OBJECTID >= 1 AND OBJECTID <= 5",
        "(1=1) AND OBJECTID >= 8 AND OBJECTID <= 34",
        "(1=1) AND OBJECTID >= 55 AND OBJECTID <= 89",
    ]


def test_id_ranges_work_without_pagination_support():
    assert sorted(fetch(FakeServer(pagination=False))["OBJECTID"]) == IDS


def test_batch_size_override():
    server = FakeServer(max_record_count=100)
    fetch(server, batch_size=3)
    assert len(server.query_calls()) == 4


def test_layer_where_is_kept():
    server = FakeServer()
    fetch(server, where="NAME LIKE 'T%'")
    assert server.query_calls()[0]["where"].startswith("(NAME LIKE 'T%') AND ")


def test_truncated_batch_is_split():
    # Advertises 4 per request but only returns 2
    gdf = fetch(FakeServer(max_record_count=4, hidden_limit=2))
    assert sorted(gdf["OBJECTID"]) == IDS
    assert not gdf["OBJECTID"].duplicated().any()


def test_offset_paging_without_oid():
    server = FakeServer(has_oid=False)
    assert sorted(fetch(server)["OBJECTID"]) == IDS
    assert [p["resultOffset"] for p in server.query_calls()] == [0, 4, 8]


def test_single_query_fallback_returns_what_server_gives():
    assert len(fetch(FakeServer(has_oid=False, pagination=False))) == 4


def test_offset_paging_gives_up_on_broken_server(monkeypatch):
    # Claims pagination support but ignores resultOffset
    server = FakeServer(has_oid=False, pagination=False)
    meta = {**server.metadata, "advancedQueryCapabilities": {"supportsPagination": True}}
    monkeypatch.setattr(client, "MAX_PAGES", 5)
    with pytest.raises(client.ArcGISError, match="gave up after 5 pages"):
        client.fetch_features(server, LAYER_URL, meta)


def test_empty_layer():
    gdf = fetch(FakeServer(ids=[]))
    assert gdf.empty
    assert gdf.crs.to_epsg() == 4326


def test_preview():
    gdf = client.preview_features(FakeServer(), LAYER_URL, limit=3)
    assert len(gdf) == 3


def test_get_ids_sorted():
    assert client.get_ids(FakeServer(), LAYER_URL) == IDS


def test_get_count_passes_timeout():
    server = FakeServer()
    seen = {}
    original = server.get

    def get(url, params=None, timeout=None):
        seen["timeout"] = timeout
        return original(url, params, timeout)

    server.get = get
    assert client.get_count(server, LAYER_URL, timeout=7) == len(IDS)
    assert seen["timeout"] == 7


def test_error_payload_raises():
    server = FakeServer()
    server.get = lambda *a, **kw: FakeResponse(
        {"error": {"code": 400, "message": "Invalid query", "details": ["bad where"]}}
    )
    with pytest.raises(client.ArcGISError, match="400 Invalid query bad where"):
        client.get_metadata(server, LAYER_URL)


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        ({"objectIdField": "FID"}, "FID"),
        (
            {
                "fields": [
                    {"name": "X", "type": "esriFieldTypeString"},
                    {"name": "OID", "type": "esriFieldTypeOID"},
                ]
            },
            "OID",
        ),
        ({"fields": []}, None),
    ],
)
def test_object_id_field(metadata, expected):
    assert client.object_id_field(metadata) == expected


def test_session_has_retries_and_user_agent():
    session = client.make_session(retries=2)
    assert session.headers["User-Agent"].startswith("Mozilla/5.0")
    assert "sd-gis-hub" not in session.headers["User-Agent"]
    assert session.get_adapter("https://x").max_retries.total == 2


@pytest.mark.network
def test_live_layer():
    url = (
        "https://www.1stdistrict.org/arcgis/rest/services/Lawrence/lawrencemapnet2/FeatureServer/0"
    )
    session = client.make_session()
    meta = client.get_metadata(session, url)
    gdf = client.fetch_features(session, url, meta, where="OBJECTID <= 25", batch_size=10)
    assert len(gdf) == client.get_count(session, url, "OBJECTID <= 25")
