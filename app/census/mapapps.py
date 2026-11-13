"""Register county map apps as census sources, from the app list saved from the host's home
page."""

from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.utils.text import slugify

from census.models import Publisher, Source
from places.models import Jurisdiction
from sdgis.mapapps.clients import MapApp, parse_apps, place_name

APPS_FILE = Path(settings.BASE_DIR) / "seeds" / "map-apps.html"
NOTES = (
    "A county map app: the county's GIS data and property records on a hosted site. The hub "
    "links to it; its features are read by the gis-data record set, not crawled here."
)


def find_jurisdiction(client: MapApp) -> Jurisdiction | None:
    return Jurisdiction.objects.filter(slug=slugify(place_name(client))).first()


@transaction.atomic
def register(clients: list[MapApp]) -> dict:
    created, unmatched = 0, []
    for client in clients:
        place = find_jurisdiction(client)
        if place is None:
            unmatched.append(client.name)
        publisher, _ = Publisher.objects.get_or_create(
            name=place.name if place else place_name(client),
            defaults={"jurisdiction": place},
        )
        _, new = Source.objects.update_or_create(
            platform=Source.Platform.MAP_APP,
            external_id=str(client.app_id),
            defaults={
                "name": f"{place_name(client)} (map app)",
                "slug": f"{slugify(place_name(client))}-map-app",
                "url": client.url(),
                "publisher": publisher,
                "jurisdiction": place,
                "crawl_schedule": "",
                "crawl_counts": False,
                "found_via": "map app list (seeds/map-apps.html)",
                "notes": NOTES,
            },
        )
        created += new
    return {"clients": len(clients), "created": created, "unmatched": unmatched}


def load(path: Path = APPS_FILE, state: str = "South Dakota") -> dict:
    return register(parse_apps(path.read_text(), state=state))
