import pytest

from sdgis.arcgis import client
from sdgis.testing import IDS, LAYER_URL, FakeResponse, FakeServer  # noqa: F401  (re-exported for tests)


@pytest.fixture
def server(monkeypatch) -> FakeServer:
    """Route all ArcGIS HTTP to a FakeServer. Tests can reassign attributes on it."""
    fake = FakeServer()
    monkeypatch.setattr(client, "make_session", lambda *a, **kw: fake)
    return fake


@pytest.fixture(autouse=True)
def no_notifications(settings):
    settings.NOTIFY = False


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
