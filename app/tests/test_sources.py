import pytest

from census import crawl, sources, views
from census.models import Dataset, Jurisdiction, Publisher, Source, name_keys
from platforms.arcgis import client as arcgis_client
from tests.platforms.test_directory import ROOT, FakeDirectory

NEW_ROOT = "https://gis.example.org/server/rest/services"
NEW_ROUTES = {
    NEW_ROOT: {
        "currentVersion": 11.3,
        "folders": ["Hosted"],
        "services": [
            {"name": "TEST1_FSA_2021", "type": "MapServer"},
            {"name": "TEST_COUNTY_WEB_LAYERS", "type": "MapServer"},
            {"name": "Test_locator", "type": "GeocodeServer"},
            {"name": "SOIL", "type": "MapServer"},
        ],
    },
    f"{NEW_ROOT}/Hosted": {
        "folders": [],
        "services": [{"name": "Hosted/Roads", "type": "FeatureServer"}],
    },
}


@pytest.fixture
def new_server(monkeypatch) -> FakeDirectory:
    fake = FakeDirectory(NEW_ROUTES)
    monkeypatch.setattr(arcgis_client, "make_session", lambda *a, **kw: fake)
    return fake


@pytest.fixture
def crawls(monkeypatch) -> list:
    calls = []
    monkeypatch.setattr(views, "start_crawl", lambda s: calls.append(s.pk))
    return calls


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (f"{NEW_ROOT}/", [NEW_ROOT]),
        (f"{NEW_ROOT}/Hosted/Roads/FeatureServer/0?f=pjson", [NEW_ROOT]),
        (
            "gis.example.org",
            [
                "https://gis.example.org/arcgis/rest/services",
                "https://gis.example.org/server/rest/services",
                "https://gis.example.org/rest/services",
            ],
        ),
        (
            "https://gis.example.org/server",
            [
                "https://gis.example.org/server/rest/services",
                "https://gis.example.org/arcgis/rest/services",
                "https://gis.example.org/rest/services",
            ],
        ),
    ],
)
def test_candidate_roots(text, expected):
    assert sources.candidate_roots(text) == expected


def test_name_keys():
    assert name_keys("BON_HOMME1_FSA_2021") == ["bon_homme_fsa", "bon_homme", "bon"]
    assert name_keys("Lawrence") == ["lawrence"]
    assert name_keys("") == []


def test_unique_slug(db):
    assert sources.unique_slug("gis.districtiii.org") == "gis-districtiii-org"


def test_platform_for():
    assert sources.platform_for(NEW_ROOT) == "arcgis_server"
    agol = "https://services3.arcgis.com/abc123/ArcGIS/rest/services"
    assert sources.platform_for(agol) == "arcgis_online"
    assert sources.default_name(agol) == "ArcGIS Online org abc123"


def test_discover_from_bare_host(new_server, county):
    found = sources.discover("gis.example.org")

    assert found.url == NEW_ROOT, "falls through /arcgis to /server"
    assert found.version == "11.3"
    assert len(found.services) == 5 and found.n_crawlable == 4
    assert found.places == {"test": (county, 2)}, "TEST1_FSA and TEST_COUNTY share a key"
    assert found.unmatched == 2


def test_discover_rejects_non_directory(new_server, db):
    with pytest.raises(sources.NotADirectory):
        sources.discover("https://elsewhere.example.org/")


def test_jurisdiction_for_matches_folders_and_service_names(source, county):
    other = Jurisdiction.objects.create(
        kind="county", geoid="46998", name="Bon Homme County", slug="bon-homme-county"
    )
    source.folder_jurisdictions = {"Bon_Homme": other.slug, "test_parcels": "missing"}
    assert source.jurisdiction_for("", "BON_HOMME1_FSA_2021") == other
    assert source.jurisdiction_for("bon homme/x", "parcels") == other, "folders match too"
    assert source.jurisdiction_for("", "SOIL") == county, "falls back to the source's place"


def test_crawl_credits_service_name_prefix(county, monkeypatch):
    monkeypatch.setattr(arcgis_client, "make_session", lambda *a, **kw: FakeDirectory())
    other = Jurisdiction.objects.create(
        kind="county", geoid="46998", name="Other County", slug="other-county"
    )
    source = Source.objects.create(
        name="Example",
        slug="example",
        url=ROOT,
        platform="arcgis_server",
        jurisdiction=county,
        folder_jurisdictions={"basemap": other.slug},
    )
    crawl.crawl_source(source)

    roads = Dataset.objects.get(url=f"{ROOT}/Basemap/MapServer/1")
    parcels = Dataset.objects.get(url=f"{ROOT}/County/parcels/FeatureServer/0")
    assert roads.jurisdiction == other
    assert parcels.jurisdiction == county


def test_add_source_preview(client, new_server, county):
    response = client.get("/sources/add/", {"url": NEW_ROOT})

    assert response.status_code == 200
    page = response.content.decode()
    assert 'value="test-county"' in page and "gis.example.org" in page
    assert not Source.objects.exists(), "the preview saves nothing"


def test_add_source_creates_and_crawls(client, new_server, county, crawls):
    response = client.post(
        "/sources/add/",
        {
            "url": NEW_ROOT,
            "name": "District X",
            "publisher": "District X Planning",
            "jurisdiction": "",
            "found_via": "a tip",
            "key": ["test", "", "soil"],
            "place": ["test-county", "", ""],
        },
    )

    source = Source.objects.get()
    assert response.status_code == 302 and response.url == source.get_absolute_url()
    assert source.slug == "district-x" and source.platform == "arcgis_server"
    assert source.publisher == Publisher.objects.get(name="District X Planning")
    assert source.jurisdiction is None
    assert source.folder_jurisdictions == {"test": "test-county"}, "rows without a place drop"
    assert crawls == [source.pk]


def test_add_source_rejects_unknown_place(client, new_server, county, crawls):
    response = client.post(
        "/sources/add/",
        {"url": NEW_ROOT, "name": "District X", "key": ["test"], "place": ["nowhere"]},
    )

    assert response.status_code == 200
    assert "No place with the slug" in response.content.decode()
    assert not Source.objects.exists() and not crawls


def test_add_existing_source_redirects(client, new_server, source):
    response = client.get("/sources/add/", {"url": "http://example.com/arcgis/rest/services"})
    assert response.status_code == 302 and response.url == source.get_absolute_url()


def test_add_source_bad_url(client, new_server, db):
    response = client.get("/sources/add/", {"url": "https://elsewhere.example.org"}, follow=True)
    assert "No ArcGIS services directory found" in response.content.decode()
