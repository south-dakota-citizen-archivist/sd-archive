"""Load South Dakota jurisdictions and boundaries from Census cartographic boundary files."""

import logging

import geopandas as gpd
from django.contrib.gis.geos import GEOSGeometry, MultiPolygon
from django.db import connection, transaction
from django.utils.text import slugify

from census.models import Jurisdiction

logger = logging.getLogger(__name__)

STATE_FIPS = "46"
YEAR = 2024
BASE = f"https://www2.census.gov/geo/tiger/GENZ{YEAR}/shp"
FILES = {
    "state": f"{BASE}/cb_{YEAR}_us_state_500k.zip",
    "county": f"{BASE}/cb_{YEAR}_us_county_500k.zip",
    "place": f"{BASE}/cb_{YEAR}_{STATE_FIPS}_place_500k.zip",
    "tribal": f"{BASE}/cb_{YEAR}_us_aiannh_500k.zip",
}
# Census designated places are statistical areas, not governments
CDP_LSAD = "57"
EQUAL_AREA_CRS = 5070  # CONUS Albers
MIN_TRIBAL_OVERLAP_M2 = 500_000


def read(url: str) -> gpd.GeoDataFrame:
    return gpd.read_file(f"/vsizip//vsicurl/{url}").to_crs(4326)


def multipolygon(geom) -> MultiPolygon:
    g = GEOSGeometry(memoryview(geom.wkb), srid=4326)
    return g if isinstance(g, MultiPolygon) else MultiPolygon(g, srid=4326)


def upsert(kind: str, geoid: str, name: str, short_name: str, slug: str, geom, parent=None):
    obj, _ = Jurisdiction.objects.update_or_create(
        kind=kind,
        geoid=geoid,
        defaults={
            "name": name,
            "short_name": short_name,
            "slug": slug,
            "boundary": multipolygon(geom),
            "parent": parent,
        },
    )
    return obj


@transaction.atomic
def load() -> dict[str, int]:
    counts = {}

    states = read(FILES["state"])
    sd = states[states["STATEFP"] == STATE_FIPS].iloc[0]
    state = upsert("state", STATE_FIPS, sd["NAME"], sd["NAME"], slugify(sd["NAME"]), sd.geometry)
    counts["state"] = 1

    counties = read(FILES["county"])
    counties = counties[counties["STATEFP"] == STATE_FIPS]
    for row in counties.itertuples():
        upsert(
            "county", row.GEOID, row.NAMELSAD, row.NAME, slugify(row.NAMELSAD), row.geometry, state
        )
    counts["county"] = len(counties)

    places = read(FILES["place"])
    places = places[places["LSAD"] != CDP_LSAD]
    for row in places.itertuples():
        upsert("place", row.GEOID, row.NAMELSAD, row.NAME, slugify(row.NAME), row.geometry, state)
    counts["place"] = len(places)

    # Keep areas with land in SD, not ones that only touch the border (Santee, Neb.)
    tribal = read(FILES["tribal"])
    overlap = tribal.intersection(sd.geometry).to_crs(EQUAL_AREA_CRS).area
    tribal = tribal[overlap > MIN_TRIBAL_OVERLAP_M2]
    for row in tribal.itertuples():
        upsert(
            "tribal", row.GEOID, row.NAMELSAD, row.NAME, slugify(row.NAMELSAD), row.geometry, state
        )
    counts["tribal"] = len(tribal)

    # A city's parent is the county containing it (by a point inside its boundary)
    with connection.cursor() as cur:
        cur.execute(
            """
            UPDATE census_jurisdiction p SET parent_id = c.id
            FROM census_jurisdiction c
            WHERE p.kind = 'place' AND c.kind = 'county'
              AND ST_Contains(c.boundary, ST_PointOnSurface(p.boundary))
            """
        )
    logger.info("Loaded jurisdictions: %s", counts)
    return counts
