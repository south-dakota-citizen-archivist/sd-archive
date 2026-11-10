import pytest

from sdgis.arcgis import client, directory
from sdgis.testing import MAPSERVER_EXTRAS, PARCEL_FIELDS, ROOT, ROUTES


def by_url(scan: directory.DirectoryScan) -> dict:
    return {layer.url: layer for layer in scan.layers}


def test_finds_queryable_layers_and_tables(fake_directory):
    scan = directory.scan(ROOT)
    found = by_url(scan)
    assert set(found) == {
        f"{ROOT}/Basemap/MapServer/1",
        f"{ROOT}/County/parcels/FeatureServer/0",
        f"{ROOT}/County/parcels/FeatureServer/1",
    }, "group/raster/non-queryable layers and non-map services are skipped"

    parcels = found[f"{ROOT}/County/parcels/FeatureServer/0"]
    assert parcels.kind == "layer"
    assert parcels.folder == "County"
    assert parcels.description == "Tax parcels"
    assert parcels.geometry_type == "esriGeometryPolygon"
    assert parcels.max_record_count == 2000
    assert parcels.supports_pagination is True
    assert parcels.object_id_field == "OBJECTID"
    assert parcels.count == 5000
    assert parcels.fields[2] == {"name": "OWNER", "type": "esriFieldTypeString", "alias": "Owner"}
    assert found[f"{ROOT}/County/parcels/FeatureServer/1"].kind == "table"


def test_mapserver_duplicates_collapse_to_featureserver(fake_directory):
    names = [
        (x.service, x.service_type) for x in directory.scan(ROOT).layers if x.name == "Parcels"
    ]
    assert names == [("County/parcels", "FeatureServer")]


def test_stopped_service_makes_scan_incomplete(fake_directory):
    scan = directory.scan(ROOT)
    old = next(s for s in scan.services if s.name == "County/old")
    assert "not started" in old.error
    assert not scan.complete


def test_complete_scan(fake_directory):
    fake_directory.routes[f"{ROOT}/County"] = {
        "folders": [],
        "services": [{"name": "County/parcels", "type": "FeatureServer"}],
    }
    assert directory.scan(ROOT).complete


def test_metadata_requests_use_short_timeout(fake_directory):
    directory.scan(ROOT, timeout=12)
    assert {t for _, t in fake_directory.calls} == {12}


def test_time_limit_skips_remaining_services(fake_directory):
    # A negative limit is already exceeded when the first service comes up
    scan = directory.scan(ROOT, time_limit=-1, counts=False, workers=1)
    assert scan.timed_out
    assert not scan.complete
    assert any("time limit" in s.error for s in scan.services)


def test_no_counts(fake_directory):
    scan = directory.scan(ROOT, counts=False)
    assert all(x.count is None for x in scan.layers)
    assert not any(url.endswith("/query") for url, _ in fake_directory.calls)


def test_folder_url(fake_directory):
    assert {x.service for x in directory.scan(f"{ROOT}/County").layers} == {"County/parcels"}


def test_service_url(fake_directory):
    scan = directory.scan(f"{ROOT}/County/parcels/MapServer", counts=False)
    assert [x.url for x in scan.layers] == [
        f"{ROOT}/County/parcels/MapServer/0",
        f"{ROOT}/County/parcels/MapServer/1",
    ]


def test_unreadable_root_raises(fake_directory):
    with pytest.raises(client.ArcGISError):
        directory.scan(f"{ROOT}/Missing")


def test_unreadable_subfolder_is_reported(fake_directory):
    fake_directory.routes[ROOT] = {**ROUTES[ROOT], "folders": ["County", "Locked"]}
    scan = directory.scan(ROOT, counts=False)
    assert "Not found" in scan.folder_errors[f"{ROOT}/Locked"]
    assert not scan.complete


@pytest.mark.parametrize("url", ["https://gis.example.com/arcgis/rest/", "not a url"])
def test_bad_url(url):
    with pytest.raises(directory.DirectoryError):
        directory.split_services_url(url)


def test_field_signature_ignores_geometry_and_shape_metrics():
    assert directory.field_signature(PARCEL_FIELDS + MAPSERVER_EXTRAS) == directory.field_signature(
        PARCEL_FIELDS
    )


def test_search_index_tables_are_skipped():
    index = [
        {"name": "Suggest", "type": "esriFieldTypeString"},
        {"name": "LayerField", "type": "esriFieldTypeString"},
        {"name": "ESRI_OID", "type": "esriFieldTypeOID"},
    ]
    assert directory.is_search_index(index)
    assert not directory.is_search_index([*index, {"name": "PIN", "type": "esriFieldTypeString"}])
