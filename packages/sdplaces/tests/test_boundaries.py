import zipfile

import pytest

import sdplaces
from sdplaces import _boundaries

gpd = pytest.importorskip("geopandas")
from shapely.geometry import box  # noqa: E402


@pytest.fixture
def cache(tmp_path, monkeypatch):
    """A cache holding a fake 2014 county file: Lawrence, Shannon and a Nebraska county."""
    monkeypatch.setenv("SDPLACES_CACHE", str(tmp_path))
    shapes = gpd.GeoDataFrame(
        {"GEOID": ["46081", "46113", "31001"], "NAME": ["Lawrence", "Shannon", "Adams"]},
        geometry=[box(-104, 44, -103, 45), box(-103, 43, -102, 44), box(-99, 40, -98, 41)],
        crs=4269,
    )
    work = tmp_path / "shp"
    work.mkdir()
    shapes.to_file(work / "cb_2014_us_county_500k.shp")
    out = tmp_path / "GENZ2014" / "cb_2014_us_county_500k.zip"
    out.parent.mkdir()
    with zipfile.ZipFile(out, "w") as z:
        for f in work.iterdir():
            z.write(f, f.name)
    return tmp_path


def test_url_for_each_kind():
    assert _boundaries.census_url("county") == (
        "https://www2.census.gov/geo/tiger/GENZ2024/shp/cb_2024_us_county_500k.zip"
    )
    assert _boundaries.census_url("cdp", 2020).endswith("/GENZ2020/shp/cb_2020_46_place_500k.zip")
    with pytest.raises(ValueError):
        _boundaries.census_url("township")


def test_cached_file_is_used_without_downloading(cache, monkeypatch):
    def no_network(*a, **k):
        raise AssertionError("tried to download")

    monkeypatch.setattr(_boundaries.urllib.request, "urlopen", no_network)
    assert _boundaries.census_file("county", 2014).parent == cache / "GENZ2014"


def test_rows_are_sdplaces_places_including_historical(cache):
    g = sdplaces.boundaries("county", 2014)
    assert list(g.geoid) == ["46081", "46113"], "Nebraska's county is left out"
    assert list(g.name) == ["Lawrence County", "Shannon County"]
    assert g.crs.to_epsg() == 4326
    shannon = g.iloc[1]
    assert shannon.valid_to.isoformat() == "2015-05-01"
    assert shannon.slug == sdplaces.get("46113", kind="county").slug
