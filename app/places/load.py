"""Load South Dakota's places and their boundaries from sdplaces.

Rows (GEOIDs, names, slugs, parents, OCD IDs, dates in force, successors) come from
sdplaces' places.csv, so former places load too. Shapes come from the Census cartographic
boundary files sdplaces caches; a former place takes its shape from an older year's file
where one has it (Shannon County from 2014), and has none otherwise (Washabaugh County,
merged in 1983).
"""

import logging

from django.contrib.gis.geos import GEOSGeometry, MultiPolygon
from django.db import transaction

import sdplaces
from places.models import Jurisdiction

logger = logging.getLogger(__name__)

# In load order: parents (the state, counties) first
KINDS = ("state", "county", "place", "cdp", "tribal", "school_district", "sldu", "sldl")
# Where former places' shapes are looked for
OLDER_YEARS = (2020, 2014)


def multipolygon(geom) -> MultiPolygon:
    from shapely import force_2d

    # some years' files carry a Z coordinate (always 0); the column is 2D
    g = GEOSGeometry(memoryview(force_2d(geom).wkb), srid=4326)
    return g if isinstance(g, MultiPolygon) else MultiPolygon(g, srid=4326)


def shapes(kind: str, places: list) -> dict:
    """GEOID → geometry for these places: this year's file, then older ones for the rest."""
    found = {r.geoid: r.geometry for r in sdplaces.boundaries(kind).itertuples()}
    for year in OLDER_YEARS:
        missing = {p.geoid for p in places} - found.keys()
        if not missing:
            break
        try:
            older = sdplaces.boundaries(kind, year)
        except Exception as e:  # noqa: BLE001 (an older file that doesn't exist is fine)
            logger.info("No %s boundaries for %d: %s", kind, year, e)
            continue
        found |= {r.geoid: r.geometry for r in older.itertuples() if r.geoid in missing}
    return found


@transaction.atomic
def load(kinds: tuple[str, ...] = KINDS) -> dict[str, int]:
    counts = {}
    parents: dict[str, Jurisdiction] = {}  # GEOID → row, for the state and counties
    rows: dict[tuple[str, str], Jurisdiction] = {}
    for kind in kinds:
        places = sdplaces.all_places(kind)
        geometry = shapes(kind, places)
        for p in places:
            obj, _ = Jurisdiction.objects.update_or_create(
                kind=kind,
                geoid=p.geoid,
                defaults={
                    "geoidfq": p.geoidfq,
                    "ocd_id": p.ocd_id,
                    "name": p.name,
                    "short_name": p.short_name,
                    "slug": p.slug,
                    "valid_from": p.valid_from,
                    "valid_to": p.valid_to,
                    "boundary": multipolygon(geometry[p.geoid]) if p.geoid in geometry else None,
                    "parent": parents.get(p.parent) if p.parent else None,
                },
            )
            rows[(kind, p.geoid)] = obj
            if kind in ("state", "county"):
                parents[p.geoid] = obj
        counts[kind] = len(places)
        missing = [p.name for p in places if p.geoid not in geometry]
        if missing:
            logger.info("%s without a boundary: %s", kind, ", ".join(missing))
    for kind in kinds:
        for p in sdplaces.all_places(kind):
            successor = rows.get((kind, p.successor)) if p.successor else None
            obj = rows[(kind, p.geoid)]
            if obj.successor_id != (successor.pk if successor else None):
                obj.successor = successor
                obj.save(update_fields=["successor"])
    logger.info("Loaded places: %s", counts)
    return counts
