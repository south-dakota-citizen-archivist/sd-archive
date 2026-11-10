"""Crawl an ArcGIS REST services directory and describe every queryable layer and table.

Works for ArcGIS Server directories and ArcGIS Online/Enterprise org directories alike.
"""

import logging
import re
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime

import requests

from platforms.arcgis import client

logger = logging.getLogger(__name__)

SERVICE_TYPES = ("MapServer", "FeatureServer")
LAYER_TYPES = ("Feature Layer", "Table")
SHAPE_METRIC_RE = re.compile(r"^shape[._]*(st)?(area|length)(\(\))?$", re.IGNORECASE)
SERVICES_URL_RE = re.compile(r"^(https?://.+?/rest/services)(?:/(.*))?$", re.IGNORECASE)
# Tables that only repeat text from other layers to feed a map viewer's search box, e.g.
# First District's AutoComplete tables (Suggest "SMITH, JOHN~2", LayerField "Parcels-Owner1")
SEARCH_INDEX_FIELDS = frozenset({"suggest", "layerfield"})

DEFAULT_WORKERS = 2
# Metadata requests should answer fast. Some servers hang on broken services, and the
# default 120s x 4 attempts would stall a crawl for minutes per service.
METADATA_RETRIES = 1


class DirectoryError(ValueError):
    """Raised for a URL that isn't an ArcGIS REST services directory."""


@dataclass
class ServiceInfo:
    name: str
    type: str
    url: str
    folder: str = ""
    error: str = ""


@dataclass
class LayerInfo:
    url: str
    service: str
    service_type: str
    folder: str = ""
    layer_id: int | None = None
    kind: str = "layer"  # "layer" or "table"
    name: str = ""
    description: str = ""
    geometry_type: str = ""
    count: int | None = None
    max_record_count: int | None = None
    supports_pagination: bool | None = None
    object_id_field: str = ""
    fields: list[dict] = field(default_factory=list)  # see client.describe_fields
    edited_at: datetime | None = None  # when the data last changed, if the server says
    # Attribute fields only, for spotting the same layer published twice
    signature: frozenset[str] = frozenset()
    error: str = ""


@dataclass
class DirectoryScan:
    root: str
    services: list[ServiceInfo] = field(default_factory=list)
    layers: list[LayerInfo] = field(default_factory=list)
    folder_errors: dict[str, str] = field(default_factory=dict)  # folder URL -> error
    timed_out: bool = False

    @property
    def complete(self) -> bool:
        """True if every folder and service was read, so missing layers really are gone."""
        return not (self.timed_out or self.folder_errors or any(s.error for s in self.services))


def field_signature(fields: list[dict]) -> frozenset[str]:
    """Field names minus geometry and server-computed shape metrics, which MapServer
    lists but FeatureServer omits (Shape, Shape.STArea(), Shape__Length, ...)."""
    return frozenset(
        f["name"]
        for f in fields
        if f.get("type") != "esriFieldTypeGeometry" and not SHAPE_METRIC_RE.match(f["name"])
    )


def is_search_index(fields: list[dict]) -> bool:
    names = {f["name"].lower() for f in fields if f.get("type") != "esriFieldTypeOID"}
    return names == SEARCH_INDEX_FIELDS


def split_services_url(url: str) -> tuple[str, str]:
    """Split into the services root and the folder/service path under it."""
    m = SERVICES_URL_RE.match(url.rstrip("/"))
    if not m:
        raise DirectoryError(
            f"expected an ArcGIS services URL like https://host/arcgis/rest/services: {url}"
        )
    return m[1], m[2] or ""


def describe(e: Exception) -> str:
    return f"{type(e).__name__}: {e}"


def list_services(
    session: requests.Session, root: str, folder: str = "", timeout: float = client.TIMEOUT
) -> tuple[list[dict], dict[str, str]]:
    """Every service under a folder (or the root), plus errors for unreadable subfolders."""
    services, errors = [], {}
    pending, seen = [folder], set()
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        url = f"{root}/{current}" if current else root
        try:
            data = client.get_json(session, url, timeout=timeout)
        except Exception as e:
            if current == folder:
                raise
            errors[url] = describe(e)
            continue
        services += data.get("services", [])
        # Folder names come back as full paths from the root
        pending += data.get("folders", [])
    return services, errors


def inspect_service(
    session: requests.Session, root: str, service: dict, timeout: float = client.TIMEOUT
) -> tuple[ServiceInfo, list[LayerInfo]]:
    name, service_type = service["name"], service["type"]
    url = f"{root}/{name}/{service_type}"
    info = ServiceInfo(name=name, type=service_type, url=url, folder=name.rpartition("/")[0])

    try:
        data = client.get_json(session, f"{url}/layers", timeout=timeout)
    except Exception as e:
        info.error = describe(e)
        return info, []

    found = []
    for kind, items in (("layer", data.get("layers", [])), ("table", data.get("tables", []))):
        for item in items:
            # Skip group, raster and annotation layers, anything that can't be queried, and
            # search indexes
            if item.get("type") not in LAYER_TYPES or "Query" not in item.get(
                "capabilities", "Query"
            ):
                continue
            fields = item.get("fields") or []
            if is_search_index(fields):
                continue
            found.append(
                LayerInfo(
                    url=f"{url}/{item['id']}",
                    service=name,
                    service_type=service_type,
                    folder=info.folder,
                    layer_id=item["id"],
                    kind=kind,
                    name=item.get("name", ""),
                    description=item.get("description") or "",
                    geometry_type=item.get("geometryType") or "",
                    max_record_count=item.get("maxRecordCount"),
                    supports_pagination=client.supports_pagination(item),
                    object_id_field=client.object_id_field(item) or "",
                    fields=client.describe_fields(item),
                    edited_at=client.last_edit(item),
                    signature=field_signature(fields),
                )
            )
    return info, found


def dedupe(layers: list[LayerInfo]) -> list[LayerInfo]:
    """Collapse layers published by both a MapServer and a FeatureServer, keeping the
    FeatureServer copy. Layers count as the same if their service, name and attribute
    fields match."""
    groups: dict[tuple, list[LayerInfo]] = {}
    for layer in layers:
        groups.setdefault((layer.service, layer.kind, layer.name, layer.signature), []).append(
            layer
        )
    return [
        min(group, key=lambda x: x.service_type != "FeatureServer") for group in groups.values()
    ]


def scan(
    url: str,
    counts: bool = True,
    workers: int = DEFAULT_WORKERS,
    timeout: float = client.METADATA_TIMEOUT,
    time_limit: float | None = None,
) -> DirectoryScan:
    """Describe every queryable layer under a services directory, folder or single service.

    time_limit (seconds) bounds the whole crawl. Services not reached in time are
    reported with an error instead of being silently dropped.
    """
    root, path = split_services_url(url)
    result = DirectoryScan(root=root)
    started = time.monotonic()

    def out_of_time() -> bool:
        return time_limit is not None and time.monotonic() - started > time_limit

    local = threading.local()

    def session() -> requests.Session:
        # requests.Session isn't guaranteed thread-safe, so give each worker its own
        if not hasattr(local, "session"):
            local.session = client.make_session(retries=METADATA_RETRIES)
        return local.session

    def run(fn: Callable, items: list) -> list:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(fn, items))

    service_path, _, last = path.rpartition("/")
    if last in SERVICE_TYPES:
        services = [{"name": service_path, "type": last}]
    else:
        services, result.folder_errors = list_services(session(), root, path, timeout)
    services = [s for s in services if s["type"] in SERVICE_TYPES]
    logger.info("Inspecting %d map/feature services under %s", len(services), url)

    def inspect(s: dict) -> tuple[ServiceInfo, list[LayerInfo]]:
        if out_of_time():
            result.timed_out = True
            info = ServiceInfo(
                name=s["name"],
                type=s["type"],
                url=f"{root}/{s['name']}/{s['type']}",
                folder=s["name"].rpartition("/")[0],
                error="skipped: crawl time limit reached",
            )
            return info, []
        return inspect_service(session(), root, s, timeout)

    found = []
    for info, layers in run(inspect, services):
        result.services.append(info)
        found += layers
    result.layers = dedupe(found)

    if counts:
        logger.info("Counting features in %d layers", len(result.layers))

        def count(layer: LayerInfo) -> None:
            if out_of_time():
                result.timed_out = True
                return
            try:
                layer.count = client.get_count(session(), layer.url, timeout=timeout)
            except Exception as e:
                layer.error = describe(e)

        run(count, result.layers)

    result.services.sort(key=lambda s: (s.name.lower(), s.type))
    result.layers.sort(
        key=lambda x: (
            x.service.lower(),
            x.service_type,
            x.kind,
            -1 if x.layer_id is None else x.layer_id,
            x.url,
        )
    )
    return result
