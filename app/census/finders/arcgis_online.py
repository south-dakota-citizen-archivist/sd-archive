"""Find services directories through ArcGIS Online's public search.

1. Search public Feature Service, Map Service and Web Map items whose extent touches South
   Dakota. A search answers at most 10,000 results, so a search that hits that is split by
   the items' modified dates until each part fits.
2. Keep the items that are mostly inside the state (at least half of the extent's area, or
   the point, inside the state's box), which drops national and worldwide items.
3. A service item's address names its services directory. A web map's layers name theirs:
   each local web map's definition is read (and kept, by item and modified time) and every
   layer and table address in it is followed to its root.
4. Roots on national or basemap hosts are skipped (they're federal or Esri's, not South
   Dakota's), and so are tile-only hosts, which have nothing to crawl.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter, defaultdict
from urllib.parse import urlsplit

from sdgis.arcgis import directory

from . import Getter, Lead, cache_dir

logger = logging.getLogger(__name__)

NAME = "ArcGIS Online"
SEARCH = "https://www.arcgis.com/sharing/rest/search"
ITEM_DATA = "https://www.arcgis.com/sharing/rest/content/items/{id}/data"
PORTAL = "https://www.arcgis.com/sharing/rest/portals/{org}"
SD_BOX = (-104.06, 42.48, -96.43, 45.95)
SERVICE_TYPES = ("Feature Service", "Map Service")
MAP_TYPES = ("Web Map",)
CAP = 10_000
PAGE = 100
DAY_MS = 86_400_000
# Hosts that serve national or basemap layers, or only tiles
SKIP_HOST = re.compile(
    r"(^|\.)(arcgisonline\.com|basemaps\.arcgis\.com|tiles\d*\.arcgis\.com|esri\.com"
    r"|(usgs|fema|epa|blm|fws|nps|usda|fs\.usda|noaa|weather|census|nationalmap|doi|nasa"
    r"|army|usace\.army|bia|ihs|dot|bts|hud|energy|eia|nrcs)\.gov)$",
    re.IGNORECASE,
)
HOSTED = re.compile(r"^(https://services\d*\.arcgis\.com/[^/]+)/arcgis/rest/services",
                    re.IGNORECASE)  # fmt: skip


def local(extent, box=SD_BOX, share: float = 0.5) -> bool:
    """Whether an item's extent ([[xmin, ymin], [xmax, ymax]]) is mostly inside the box."""
    try:
        (x0, y0), (x1, y1) = extent
    except (TypeError, ValueError):
        return False
    bx0, by0, bx1, by1 = box
    area = (x1 - x0) * (y1 - y0)
    if area <= 0:
        return bx0 <= x0 <= bx1 and by0 <= y0 <= by1
    overlap = max(0.0, min(x1, bx1) - max(x0, bx0)) * max(0.0, min(y1, by1) - max(y0, by0))
    return overlap / area >= share


def services_root(url: str) -> str | None:
    """The services directory a service, layer or table address sits under, or None."""
    if not url or not url.lower().startswith("http"):
        return None
    try:
        root = directory.split_services_url(url.split("?")[0])[0]
    except directory.DirectoryError:
        return None
    hosted = HOSTED.match(root)
    if hosted:  # one spelling for an organization's hosted directory
        return f"{hosted[1]}/arcgis/rest/services"
    parts = urlsplit(root)
    return f"https://{parts.netloc.lower()}{parts.path}" if parts.scheme else None


def layer_urls(definition: dict) -> list[str]:
    """Every layer and table address in a web map's definition, group layers included."""
    found = []

    def walk(layers):
        for layer in layers or []:
            if isinstance(layer, dict):
                if layer.get("url"):
                    found.append(layer["url"])
                walk(layer.get("layers"))

    walk(definition.get("operationalLayers"))
    walk(definition.get("tables"))
    return found


def _stamp(ms: int) -> str:
    return f"{ms:019d}"


def search_all(get: Getter, query: str, start_ms: int = 0, end_ms: int | None = None,
               box=SD_BOX) -> list[dict]:  # fmt: skip
    """Every item a search finds in the box, split by modified date where it would pass the
    10,000-result cap."""
    import time as _time

    end_ms = end_ms if end_ms is not None else int(_time.time() * 1000) + DAY_MS
    params = {"q": f"{query} AND modified:[{_stamp(start_ms)} TO {_stamp(end_ms)}]",
              "bbox": ",".join(map(str, box)), "f": "json", "num": PAGE,
              "sortField": "modified", "sortOrder": "asc"}  # fmt: skip
    total = get.json(SEARCH, {**params, "num": 1})["total"]
    if total >= CAP and end_ms - start_ms > DAY_MS:
        middle = (start_ms + end_ms) // 2
        return (search_all(get, query, start_ms, middle, box)
                + search_all(get, query, middle + 1, end_ms, box))  # fmt: skip
    items, start = [], 1
    while start > 0 and start <= CAP - PAGE + 1:
        answer = get.json(SEARCH, {**params, "start": start})
        items += answer.get("results", [])
        start = answer.get("nextStart", -1)
    return items


def modified(ms) -> str | None:
    """An item's modified time (epoch milliseconds) as a date."""
    from datetime import UTC, datetime

    return datetime.fromtimestamp(ms / 1000, UTC).date().isoformat() if ms else None


def web_map(get: Getter, item: dict) -> dict:
    """A web map's definition, read once per modified time."""
    path = cache_dir("arcgis-online") / f"webmap-{item['id']}-{item.get('modified', 0)}.json"
    if path.exists():
        return json.loads(path.read_text())
    try:
        definition = get.json(ITEM_DATA.format(id=item["id"]), {"f": "json"})
    except Exception as e:  # a map that can't be read is noted by its absence
        logger.info("web map %s: %s", item["id"], e)
        return {}
    if not isinstance(definition, dict):
        return {}
    path.write_text(json.dumps(definition))
    return definition


def org_name(get: Getter, root: str) -> str:
    hosted = HOSTED.match(root)
    if not hosted:
        return ""
    try:
        return get.json(PORTAL.format(org=hosted[1].rsplit("/", 1)[1]), {"f": "json"}).get(
            "name", "")  # fmt: skip
    except Exception:
        return ""


def find(get: Getter | None = None) -> tuple[list[Lead], dict]:
    get = get or Getter(pause=0.7)
    items: dict[str, dict] = {}
    for kind in SERVICE_TYPES + MAP_TYPES:
        for item in search_all(get, f'type:"{kind}" AND access:public'):
            items[item["id"]] = item
    near = [i for i in items.values() if local(i.get("extent"))]
    evidence: dict[str, list[dict]] = defaultdict(list)
    skipped: Counter = Counter()

    def note(url, example):
        root = services_root(url)
        if root is None:
            return
        if SKIP_HOST.search(urlsplit(root).hostname or ""):
            skipped[urlsplit(root).hostname] += 1
            return
        evidence[root].append(example)

    maps_read = 0
    for item in near:
        about = {"item": item["id"], "title": item.get("title", ""),
                 "owner": item.get("owner", ""), "type": item["type"],
                 "modified": modified(item.get("modified"))}  # fmt: skip
        if item["type"] in SERVICE_TYPES:
            note(item.get("url"), {**about, "layer": item.get("url")})
        else:
            maps_read += 1
            for url in dict.fromkeys(layer_urls(web_map(get, item))):
                note(url, {**about, "layer": url})
    leads = []
    for root, examples in evidence.items():
        owners = Counter(e["owner"] for e in examples if e["owner"])
        title = org_name(get, root) or ", ".join(o for o, _ in owners.most_common(2))
        leads.append(Lead(root, NAME, title=title, evidence=examples))
    stats = {"items": len(items), "in_south_dakota": len(near), "web_maps_read": maps_read,
             "roots": len(leads), "skipped_hosts": dict(skipped.most_common(15))}  # fmt: skip
    return leads, stats
