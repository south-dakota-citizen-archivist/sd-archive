import geopandas as gpd
import pytest
from shapely.geometry import MultiPolygon, box

from census import jurisdictions
from census.models import Jurisdiction

COLUMNS = ["kind", "geoid", "name", "short_name", "slug", "parent", "valid_from", "valid_to"]


def frame(kind, rows):
    """rows: (geoid, name, short_name, slug, parent, geometry)."""
    records = [
        dict(zip(COLUMNS, [kind, *r, None, None], strict=True)) | {"geometry": g} for *r, g in rows
    ]
    return gpd.GeoDataFrame(records, columns=[*COLUMNS, "geometry"], geometry="geometry", crs=4326)


@pytest.fixture
def fake_boundaries(monkeypatch):
    sq = box(-104, 44, -103, 45)
    town = MultiPolygon([box(-103.9, 44.4, -103.8, 44.5)])
    frames = {
        "state": frame("state", [("46", "South Dakota", "South Dakota", "south-dakota", "", sq)]),
        "county": frame(
            "county", [("46081", "Lawrence County", "Lawrence", "lawrence-county", "46", sq)]
        ),
        "place": frame(
            "place", [("4660020", "Spearfish city", "Spearfish", "spearfish", "46081", town)]
        ),
        "tribal": frame("tribal", []),
    }
    monkeypatch.setattr(jurisdictions.sdplaces, "boundaries", lambda kind: frames[kind])


def test_load_sets_parents_from_sdplaces(db, fake_boundaries):
    assert jurisdictions.load() == {"state": 1, "county": 1, "place": 1, "tribal": 0}
    spearfish = Jurisdiction.objects.get(kind="place", geoid="4660020")
    assert spearfish.parent.slug == "lawrence-county"
    assert spearfish.parent.parent.kind == "state"
    assert spearfish.boundary.geom_type == "MultiPolygon"


def test_load_again_updates_in_place(db, fake_boundaries):
    jurisdictions.load()
    jurisdictions.load()
    assert Jurisdiction.objects.count() == 3
