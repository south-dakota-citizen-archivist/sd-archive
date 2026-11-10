"""Load South Dakota jurisdictions and their boundaries from sdplaces.

GEOIDs, names, slugs and parents come from sdplaces' places.csv, and shapes from the Census
cartographic boundary files it caches (`sdplaces.boundaries`). Census designated places are
statistical areas, not governments, so they aren't loaded.
"""

import logging

from django.contrib.gis.geos import GEOSGeometry, MultiPolygon
from django.db import transaction

import sdplaces
from census.models import Jurisdiction

logger = logging.getLogger(__name__)

# Jurisdiction kinds, in load order (parents first)
KINDS = ("state", "county", "place", "tribal")


def multipolygon(geom) -> MultiPolygon:
    g = GEOSGeometry(memoryview(geom.wkb), srid=4326)
    return g if isinstance(g, MultiPolygon) else MultiPolygon(g, srid=4326)


@transaction.atomic
def load() -> dict[str, int]:
    counts = {}
    loaded: dict[str, Jurisdiction] = {}  # GEOID → row, for parents (counties and cities differ)
    for kind in KINDS:
        shapes = sdplaces.boundaries(kind)
        for row in shapes.itertuples():
            obj, _ = Jurisdiction.objects.update_or_create(
                kind=kind,
                geoid=row.geoid,
                defaults={
                    "name": row.name,
                    "short_name": row.short_name,
                    "slug": row.slug,
                    "boundary": multipolygon(row.geometry),
                    "parent": loaded.get(row.parent) if row.parent else None,
                },
            )
            if kind in ("state", "county"):
                loaded[row.geoid] = obj
        counts[kind] = len(shapes)
    logger.info("Loaded jurisdictions: %s", counts)
    return counts
