"""Add a source from a pasted URL: find its services directory, list what it publishes and
suggest which places its folders and services belong to."""

import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from django.db.models import Q
from django.utils.text import slugify

from census.models import Jurisdiction, Source, name_keys
from sdgis.arcgis import client, directory

# Where ArcGIS Server and Enterprise usually put the services directory
COMMON_PATHS = ("arcgis/rest/services", "server/rest/services", "rest/services")
ARCGIS_ONLINE_HOST_RE = re.compile(r"(^|\.)arcgis\.com$", re.IGNORECASE)


class NotADirectory(ValueError):
    """Raised when no ArcGIS services directory answers at or near the URL."""


@dataclass
class Discovery:
    url: str  # the services root
    platform: str
    version: str = ""
    folders: list[str] = field(default_factory=list)
    services: list[dict] = field(default_factory=list)  # {"name", "type"}, every folder
    folder_errors: dict[str, str] = field(default_factory=dict)
    # Folder or service-name key -> (jurisdiction, services it covers)
    places: dict[str, tuple[Jurisdiction, int]] = field(default_factory=dict)

    @property
    def platform_label(self) -> str:
        return Source.Platform(self.platform).label

    @property
    def service_types(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for s in self.services:
            counts[s["type"]] = counts.get(s["type"], 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: -kv[1]))

    @property
    def n_crawlable(self) -> int:
        return sum(s["type"] in directory.SERVICE_TYPES for s in self.services)

    @property
    def unmatched(self) -> int:
        """Crawlable services no suggested key covers."""
        return self.n_crawlable - sum(n for _, n in self.places.values())


def candidate_roots(text: str) -> list[str]:
    """Services roots to try for a pasted URL: the root of a services/folder/layer URL, or
    the usual locations on a bare host."""
    text = text.strip()
    if not re.match(r"^https?://", text, re.IGNORECASE):
        text = f"https://{text}"
    parts = urlsplit(text)
    url = f"{parts.scheme}://{parts.netloc}{parts.path}".rstrip("/")
    try:
        return [directory.split_services_url(url)[0]]
    except directory.DirectoryError:
        pass
    base = f"{parts.scheme}://{parts.netloc}"
    candidates = [f"{url}/rest/services"] if parts.path.strip("/") else []
    candidates += [f"{base}/{path}" for path in COMMON_PATHS]
    return list(dict.fromkeys(candidates))


def existing_source(root: str) -> Source | None:
    """A source already registered for this root, ignoring case and http/https."""
    rest = root.split("://", 1)[1]
    return Source.objects.filter(
        Q(url__iexact=f"https://{rest}") | Q(url__iexact=f"http://{rest}")
    ).first()


def platform_for(root: str) -> str:
    host = urlsplit(root).hostname or ""
    if ARCGIS_ONLINE_HOST_RE.search(host):
        return Source.Platform.ARCGIS_ONLINE
    return Source.Platform.ARCGIS_SERVER


def discover(text: str) -> Discovery:
    session = client.make_session(retries=directory.METADATA_RETRIES)
    errors = []
    for root in candidate_roots(text):
        try:
            data = client.get_json(session, root, timeout=client.METADATA_TIMEOUT)
        except Exception as e:
            errors.append(f"{root}: {directory.describe(e)}")
            continue
        if "services" not in data and "folders" not in data:
            errors.append(f"{root}: not a services directory")
            continue
        services, folder_errors = directory.list_services(
            session, root, timeout=client.METADATA_TIMEOUT
        )
        found = Discovery(
            url=root,
            platform=platform_for(root),
            version=str(data.get("currentVersion", "")),
            folders=data.get("folders", []),
            services=services,
            folder_errors=folder_errors,
        )
        found.places = suggest_places(services)
        return found
    raise NotADirectory("No ArcGIS services directory found. Tried " + "; ".join(errors))


def match_jurisdiction(key: str, places: dict[str, Jurisdiction]) -> Jurisdiction | None:
    """A county first, then a city or town: "yankton" is Yankton County, not the city."""
    slug = key.replace("_", "-")
    return places.get(f"{slug}-county") or places.get(slug)


def suggest_places(services: list[dict]) -> dict[str, tuple[Jurisdiction, int]]:
    """Map top-level folders and service-name prefixes to places by name.

    Takes the shortest prefix that names a place, so AURORA1_FSA_2021 and
    AURORA_COUNTY_WEB_LAYERS share the key "aurora", while BON_HOMME1_FSA_2021 needs
    "bon_homme". The folder wins over the service name, as it does in a crawl.
    """
    places = {j.slug: j for j in Jurisdiction.objects.all()}
    suggested: dict[str, tuple[Jurisdiction, int]] = {}
    for s in services:
        if s["type"] not in directory.SERVICE_TYPES:
            continue
        folder, _, name = s["name"].rpartition("/")
        for text in (folder.split("/")[0], name):
            hit = next(
                (
                    (key, j)
                    for key in reversed(name_keys(text))
                    if (j := match_jurisdiction(key, places))
                ),
                None,
            )
            if hit:
                key, j = hit
                suggested[key] = (j, suggested.get(key, (j, 0))[1] + 1)
                break
    return dict(sorted(suggested.items()))


def default_name(root: str) -> str:
    parts = urlsplit(root)
    if ARCGIS_ONLINE_HOST_RE.search(parts.hostname or ""):
        org = parts.path.strip("/").split("/")[0]
        return f"ArcGIS Online org {org}"
    return parts.hostname or root


def unique_slug(name: str) -> str:
    # Host names: gis.districtiii.org -> gis-districtiii-org, not gisdistrictiiiorg
    base = slugify(name.replace(".", " "))[:240] or "source"
    slug, n = base, 2
    while Source.objects.filter(slug=slug).exists():
        slug, n = f"{base}-{n}", n + 1
    return slug
