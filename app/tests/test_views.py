import json

import pytest
from django.utils import timezone

from harvest import service, views
from harvest.models import Harvest


@pytest.fixture
def harvested(dataset, server):
    service.harvest_dataset(dataset)
    server.owners[2] = "DOE, JANE"
    service.harvest_dataset(dataset)
    return dataset


@pytest.fixture
def no_celery(monkeypatch):
    from census import views

    calls = []
    monkeypatch.setattr(
        views, "start_harvest", lambda d, force=False: calls.append(("harvest", d.pk, force))
    )
    monkeypatch.setattr(views, "start_crawl", lambda s: calls.append(("crawl", s.pk)))
    return calls


def test_pages_render(client, harvested):
    pk = harvested.pk
    second = Harvest.objects.filter(dataset=harvested).first()
    pages = [
        "/",
        "/datasets/",
        "/datasets/?q=town&place=test-county&topic=&status=watched",
        f"/datasets/{pk}/",
        f"/datasets/{pk}/map/",
        f"/datasets/{pk}/table/",
        f"/datasets/{pk}/table/?sort=OBJECTID&dir=desc&q=town",
        f"/datasets/{pk}/changes/",
        f"/datasets/{pk}/download/",
        f"/datasets/{pk}/features/2/",
        f"/harvests/{second.pk}/",
        "/places/",
        "/places/test-county/",
        "/coverage/",
        "/sources/",
        "/sources/test-server/",
        "/changes/",
        "/jobs/",
        "/search/",
        "/search/?q=doe+jane",
        "/api/v1/docs",
    ]
    for url in pages:
        response = client.get(url)
        assert response.status_code == 200, url


def test_feature_page_shows_the_latest_record(client, harvested):
    body = client.get(f"/datasets/{harvested.pk}/features/2/").content.decode()
    assert "DOE, JANE" in body and "SMITH, JOHN 2" not in body
    assert "First seen" in body
    assert client.get(f"/datasets/{harvested.pk}/features/999/").status_code == 404


def test_harvest_page_lists_changed_records(client, harvested):
    latest = Harvest.objects.filter(dataset=harvested).first()
    body = client.get(f"/harvests/{latest.pk}/").content.decode()
    assert "Changed records (1)" in body and "DOE, JANE" in body


def test_table_sorts_numerically(client, harvested):
    response = client.get(f"/datasets/{harvested.pk}/table/?sort=OBJECTID&dir=desc")
    keys = [row[0] for row in response.context["rows"]]
    assert keys[:3] == ["89", "55", "34"]


def test_htmx_partials(client, harvested):
    hx = {"HTTP_HX_REQUEST": "true"}
    assert "card" in client.get("/datasets/?q=towns", **hx).content.decode()
    assert "DOE, JANE" in client.get("/search/?q=jane+doe", **hx).content.decode()


def test_preview_from_source(client, dataset):
    data = json.loads(client.get(f"/datasets/{dataset.pk}/preview.geojson").content)
    # One request, so at most the server's maxRecordCount (4 on the fake server)
    assert data["type"] == "FeatureCollection" and len(data["features"]) == 4
    body = client.get(f"/datasets/{dataset.pk}/table/preview/").content.decode()
    assert "Town 1" in body


def test_downloads(client, harvested):
    geojson = b"".join(client.get(f"/datasets/{harvested.pk}/download.geojson").streaming_content)
    assert len(json.loads(geojson)["features"]) == 10
    csv_text = b"".join(
        client.get(f"/datasets/{harvested.pk}/download.csv").streaming_content
    ).decode()
    assert csv_text.splitlines()[0] == "_key,OBJECTID,NAME,OWNER_NAM,_geometry_geojson"
    assert len(csv_text.splitlines()) == 11
    parquet = client.get(f"/datasets/{harvested.pk}/download.parquet")
    assert parquet.status_code == 200
    assert client.get(f"/datasets/{harvested.pk}/download.xlsx").status_code == 404

    day = timezone.localdate(harvested.last_harvested_at).isoformat()
    name = f"{day}-test-county-sd-towns"
    assert parquet["Content-Disposition"] == f'attachment; filename="{name}.parquet"'
    response = client.get(f"/datasets/{harvested.pk}/download.csv")
    assert response["Content-Disposition"] == f'attachment; filename="{name}.csv"'


def test_download_slug(dataset, state):
    assert views.download_slug(dataset) == "test-county-sd-towns", "no date before a harvest"
    dataset.name = "Test County Parcels"
    assert views.download_slug(dataset) == "test-county-sd-parcels"
    dataset.jurisdiction = state
    assert views.download_slug(dataset) == f"{state.slug}-test-county-parcels"
    dataset.jurisdiction = None
    assert views.download_slug(dataset) == "test-server-test-county-parcels"


def test_vector_tile(client, harvested):
    # z=6 tile covering the fake features near (-103.1, 44.1)
    response = client.get(f"/tiles/{harvested.pk}/6/13/23.pbf")
    assert response.status_code == 200
    assert response["Content-Type"] == "application/vnd.mapbox-vector-tile"
    assert len(response.content) > 0
    assert client.get(f"/tiles/{harvested.pk}/6/0/0.pbf").content == b""


def test_counties_geojson(client, dataset):
    data = json.loads(client.get("/places/counties.geojson").content)
    [county] = data["features"]
    assert county["properties"] == {"name": "Test County", "slug": "test-county", "n": 1}


def test_watch_and_harvest_buttons(client, dataset, no_celery):
    dataset.status = "discovered"
    dataset.save()
    client.post(f"/datasets/{dataset.pk}/watch/", {"watch": "1"})
    dataset.refresh_from_db()
    assert dataset.is_watched
    assert ("harvest", dataset.pk, False) in no_celery, "first watch queues a harvest"

    client.post(f"/datasets/{dataset.pk}/harvest/", {"force": "1"})
    assert ("harvest", dataset.pk, True) in no_celery
    client.post("/sources/test-server/crawl/")
    assert ("crawl", dataset.source_id) in no_celery


def test_api(client, harvested):
    pk = harvested.pk
    listing = client.get("/api/v1/datasets?q=towns").json()
    assert listing["count"] == 1 and listing["items"][0]["jurisdiction"] == "test-county"
    assert client.get(f"/api/v1/datasets/{pk}").json()["name_fields"] == ["OWNER_NAM"]

    features = client.get(f"/api/v1/datasets/{pk}/features?limit=3").json()
    assert len(features["features"]) == 3
    boxed = client.get(f"/api/v1/datasets/{pk}/features?bbox=-103.04,44.0,-103.0,44.04").json()
    assert {f["id"] for f in boxed["features"]} == {"1", "2", "3"}

    two = next(f for f in client.get(f"/api/v1/datasets/{pk}/features?limit=50").json()["features"]
               if f["id"] == "2")  # fmt: skip
    assert two["properties"]["OWNER_NAM"] == "DOE, JANE"
    assert client.get(f"/api/v1/datasets/{pk}/features/2/history").status_code == 404

    names = client.get("/api/v1/search/names?q=jane+doe").json()
    assert names[0]["name"] == "DOE, JANE" and names[0]["records"][0]["feature_key"] == "2"
    assert client.get("/api/v1/jurisdictions?kind=county").json()[0]["slug"] == "test-county"


def test_catalog_ranks_name_matches_first(client, dataset):
    from census.models import Dataset

    other = Dataset.objects.create(
        source=dataset.source,
        url=dataset.url.replace("/0", "/7"),
        name="Airports",
        fields=[],
        search_text="Airports towns_served",
        first_seen=dataset.first_seen,
        last_seen=dataset.last_seen,
    )
    names = [d.name for d in client.get("/datasets/?q=towns").context["page"]]
    assert names == ["Towns", other.name]


def test_admin_pages(admin_client, harvested):
    from harvest.models import Feature

    feature = Feature.objects.filter(dataset=harvested).first()
    urls = [
        "/admin/",
        "/admin/census/dataset/",
        f"/admin/census/dataset/{harvested.pk}/change/",
        "/admin/census/source/",
        f"/admin/census/source/{harvested.source_id}/change/",
        "/admin/places/jurisdiction/",
        f"/admin/places/jurisdiction/{harvested.jurisdiction_id}/change/",
        "/admin/census/topic/",
        "/admin/census/crawl/",
        "/admin/census/service/",
        "/admin/census/publisher/",
        "/admin/census/sourcecandidate/",
        "/admin/harvest/harvest/",
        "/admin/harvest/feature/",
        f"/admin/harvest/feature/{feature.pk}/change/",
        "/admin/search/nameentry/",
        "/admin/core/job/",
    ]
    for url in urls:
        assert admin_client.get(url).status_code == 200, url


def test_admin_actions(admin_client, dataset, monkeypatch):
    from census import admin as census_admin

    queued = []
    monkeypatch.setattr(census_admin, "start_harvest", lambda d: queued.append(d.pk))
    monkeypatch.setattr(census_admin, "start_crawl", lambda s: queued.append(("crawl", s.pk)))
    admin_client.post(
        "/admin/census/dataset/", {"action": "harvest_now", "_selected_action": [dataset.pk]}
    )
    admin_client.post(
        "/admin/census/source/", {"action": "crawl_now", "_selected_action": [dataset.source_id]}
    )
    assert queued == [dataset.pk, ("crawl", dataset.source_id)]

    admin_client.post(
        "/admin/census/dataset/", {"action": "unwatch", "_selected_action": [dataset.pk]}
    )
    dataset.refresh_from_db()
    assert not dataset.is_watched
