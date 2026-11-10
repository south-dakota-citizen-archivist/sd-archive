"""Boundaries from Census cartographic boundary files (needs the `geo` extra).

Shapes are too big to ship, so each file is downloaded once and kept in a local cache:
$SDPLACES_CACHE, or ~/.cache/sdplaces. Rows are the places in places.csv, so the GEOIDs,
names and slugs are sdplaces' own, and the Census file's other rows (CDPs in the place
file, other states, tribal areas that only touch the border) are left out.
"""

from __future__ import annotations

import os
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING

from ._places import KINDS, all_places

if TYPE_CHECKING:
    import geopandas as gpd

YEAR = 2024
BASE = "https://www2.census.gov/geo/tiger/GENZ{year}/shp"
# Census file for each kind: national files are filtered to South Dakota's places
FILES = {
    "state": "cb_{year}_us_state_500k.zip",
    "county": "cb_{year}_us_county_500k.zip",
    "place": "cb_{year}_46_place_500k.zip",
    "cdp": "cb_{year}_46_place_500k.zip",
    "tribal": "cb_{year}_us_aiannh_500k.zip",
    "school_district": "cb_{year}_46_unsd_500k.zip",
    "sldu": "cb_{year}_46_sldu_500k.zip",
    "sldl": "cb_{year}_46_sldl_500k.zip",
}
COLUMNS = ["kind", "geoid", "name", "short_name", "slug", "parent", "valid_from", "valid_to"]


def cache_dir() -> Path:
    return Path(os.environ.get("SDPLACES_CACHE") or "~/.cache/sdplaces").expanduser()


def census_url(kind: str, year: int = YEAR) -> str:
    if kind not in FILES:
        raise ValueError(f"kind must be one of {KINDS}")
    return f"{BASE.format(year=year)}/{FILES[kind].format(year=year)}"


def census_file(kind: str, year: int = YEAR) -> Path:
    """The Census boundary file for a kind of place, downloaded once into the cache."""
    url = census_url(kind, year)
    path = cache_dir() / f"GENZ{year}" / url.rsplit("/", 1)[-1]
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".partial")
    req = urllib.request.Request(url, headers={"User-Agent": "sdplaces"})
    with urllib.request.urlopen(req, timeout=300) as resp, tmp.open("wb") as f:
        while chunk := resp.read(1 << 20):
            f.write(chunk)
    tmp.replace(path)  # whole file or nothing
    return path


def boundaries(kind: str, year: int = YEAR) -> gpd.GeoDataFrame:
    """A GeoDataFrame (EPSG:4326) of every place of one kind in that year's Census file.

    Columns: kind, geoid, name, short_name, slug, parent, valid_from, valid_to, geometry.
    For an older year, historical places come back too when the file has them
    (boundaries("county", 2014) includes Shannon County, 46113). Places the file has but
    sdplaces doesn't know are left out.
    """
    import geopandas as gpd

    places = {p.geoid: p for p in all_places(kind)}
    shapes = gpd.read_file(f"zip://{census_file(kind, year)}")
    shapes = shapes[shapes["GEOID"].isin(places)].to_crs(4326)
    rows = []
    for geoid, geom in zip(shapes["GEOID"], shapes.geometry, strict=True):
        p = places[geoid]
        rows.append({c: getattr(p, c) for c in COLUMNS} | {"geometry": geom})
    out = gpd.GeoDataFrame(rows, columns=[*COLUMNS, "geometry"], geometry="geometry", crs=4326)
    return out.sort_values("name", ignore_index=True)
