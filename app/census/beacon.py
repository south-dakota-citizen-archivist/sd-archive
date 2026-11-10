"""Register Beacon apps as census sources, from the client list saved from Beacon's home page."""

from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.utils.text import slugify

from census.models import Publisher, Source
from places.models import Jurisdiction
from sdgis.beacon.clients import BeaconClient, parse_clients, place_name

CLIENTS_FILE = Path(settings.BASE_DIR) / "seeds" / "beacon-clients.html"
NOTES = (
    "Parcel search and map portal. Beacon puts every page, its API and its data extracts "
    "behind a Cloudflare bot challenge, so the hub links to it but doesn't crawl it."
)


def find_jurisdiction(client: BeaconClient) -> Jurisdiction | None:
    return Jurisdiction.objects.filter(slug=slugify(place_name(client))).first()


@transaction.atomic
def register(clients: list[BeaconClient]) -> dict:
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
            platform=Source.Platform.BEACON,
            external_id=str(client.app_id),
            defaults={
                "name": f"{place_name(client)} (Beacon)",
                "slug": f"{slugify(place_name(client))}-beacon",
                "url": client.url(),
                "publisher": publisher,
                "jurisdiction": place,
                "crawl_schedule": "",
                "crawl_counts": False,
                "found_via": "Beacon client list (seeds/beacon-clients.html)",
                "notes": NOTES,
            },
        )
        created += new
    return {"clients": len(clients), "created": created, "unmatched": unmatched}


def load(path: Path = CLIENTS_FILE, state: str = "South Dakota") -> dict:
    return register(parse_clients(path.read_text(), state=state))
