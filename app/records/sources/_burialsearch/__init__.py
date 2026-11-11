"""BurialSearch (burialsearch.com), the public burial map many cemeteries publish from their
CIMS cemetery software.

- directory: every cemetery on BurialSearch, from the list its landing page carries
- a cemetery's page: its settings (the schema, the account's user ID, the map bounds), read
  from the page's Livewire components
- layers(): every burial and space on the cemetery's map (GeoJSON), asked for the whole map
  at the closest zoom
- burials_in(): the full records of the burials in one space, from the page's space-info
  component (names, dates, veteran service, location, owner), as the page asks for them

Livewire sends arrays as [value, {"s": "arr"}] pairs; unwrap() undoes that. The map's bounds
name longitude "Lat" and latitude "Lng"; they're passed back as given.
"""

from __future__ import annotations

import html
import json
import re

from records.sources._tables import LayoutChanged

BASE_URL = "https://burialsearch.com"
CLOSEST_ZOOM = 22
_ENTRY = re.compile(r'\{"parent":(null|"[^"]*"),"schema":"([^"]+)","name":"([^"]+)",'
                    r'"address":"([^"]*)","cims_lite":(\w+),"lightmap":(\w+),'
                    r'"link_to_schema":"([^"]*)"\}')  # fmt: skip


def unwrap(value):
    """Livewire's [value, {"s": "arr"}] pairs as plain values, all the way down."""
    if isinstance(value, list):
        if len(value) == 2 and isinstance(value[1], dict) and set(value[1]) <= {"s", "class"}:
            return unwrap(value[0])
        return [unwrap(v) for v in value]
    if isinstance(value, dict):
        return {k: unwrap(v) for k, v in value.items()}
    return value


def directory(page: str) -> list[dict]:
    """Every cemetery the landing page lists: schema, name, address, and its page."""
    out = []
    for parent, schema, name, address, lite, _lightmap, link in _ENTRY.findall(html.unescape(page)):
        out.append({"schema": schema, "name": name, "address": address,
                    "parent": None if parent == "null" else parent.strip('"'),
                    "cims_lite": lite == "true", "url": f"{BASE_URL}/{link}"})  # fmt: skip
    return out


def components(page: str) -> dict[str, dict]:
    """The page's Livewire components, by name: their unwrapped data and raw snapshot."""
    out = {}
    for raw in re.findall(r'wire:snapshot="([^"]+)"', page):
        snapshot = html.unescape(raw)
        parsed = json.loads(snapshot)
        out.setdefault(parsed["memo"]["name"], {"data": unwrap(parsed["data"]),
                                                "snapshot": snapshot})  # fmt: skip
    return out


def settings(page: str) -> dict:
    """What the map's requests need: the schema group (with bounds and product), the account's
    user ID, the cemetery, the space-info component's snapshot, and the CSRF token."""
    found = components(page)
    space_info = found.get("burial-search.space-info")
    search = found.get("burial-search.search-component") or space_info
    main = found.get("burial-search")
    token = re.search(r'name="csrf-token" content="([^"]+)"', page)
    if not (space_info and search and main and token):
        raise LayoutChanged("not a BurialSearch cemetery page")
    m = re.search(r'"userCemetery":\[\{"user_id":(\d+)', html.unescape(page))
    group = search["data"]["schemaGroup"]
    cemetery = main["data"].get("cemetery") or {}
    cemetery = cemetery[0] if isinstance(cemetery, list) and cemetery else cemetery
    return {"schema_group": group, "schemas": list(group),
            "user_id": int(m.group(1)) if m else None,
            "cemetery": cemetery,
            "space_info_snapshot": space_info["snapshot"], "csrf": token.group(1)}  # fmt: skip


def bounds(schema_group: dict) -> dict:
    """The box around every schema's map bounds, padded by a tenth."""
    boxes = [s.get("bounds") for s in schema_group.values() if s.get("bounds")]
    if not boxes:
        raise LayoutChanged("no map bounds")
    xs = [b[k] for b in boxes for k in ("nwLat", "seLat")]
    ys = [b[k] for b in boxes for k in ("nwLng", "seLng")]
    pad_x, pad_y = (max(xs) - min(xs)) / 10, (max(ys) - min(ys)) / 10
    return {"nwLat": min(xs) - pad_x, "nwLng": max(ys) + pad_y,
            "seLat": max(xs) + pad_x, "seLng": min(ys) - pad_y}  # fmt: skip


def decode(value):
    """The API's JSON, some of whose values are themselves JSON strings."""
    if isinstance(value, str) and value.strip()[:1] in ("{", "["):
        try:
            return decode(json.loads(value))
        except json.JSONDecodeError:
            return value
    if isinstance(value, dict):
        return {k: decode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [decode(v) for v in value]
    return value


def features(layers: dict, kind: str) -> list[dict]:
    collection = layers.get(kind) or {}
    return collection.get("features") or [] if isinstance(collection, dict) else []


def flatten_burials(nested) -> list[dict]:
    """The burial records in the space-info component's data."""
    out = []
    if isinstance(nested, dict):
        if "burial_id" in nested:
            return [nested]
        for v in nested.values():
            out += flatten_burials(v)
    elif isinstance(nested, list):
        for v in nested:
            out += flatten_burials(v)
    return out


class Session:
    """A cemetery's page opened, and the map's requests made with its session."""

    def __init__(self, fetcher, url: str):
        self.fetcher, self.url = fetcher, url
        self.page = fetcher.request("GET", url, timeout=120).text
        self.settings = settings(self.page)

    def _headers(self, livewire: bool = False) -> dict:
        from urllib.parse import unquote

        session = getattr(self.fetcher, "session", None)
        xsrf = unquote(session.cookies.get("XSRF-TOKEN", "")) if session is not None else ""
        headers = {"X-CSRF-TOKEN": self.settings["csrf"], "X-XSRF-TOKEN": xsrf,
                   "X-Requested-With": "XMLHttpRequest", "Referer": self.url,
                   "Accept": "application/json, text/plain, */*"}  # fmt: skip
        if livewire:
            headers["X-Livewire"] = "true"
        return headers

    def layers(self) -> dict:
        box = bounds(self.settings["schema_group"])
        body = {**box, "schemaGroup": self.settings["schema_group"], "level": CLOSEST_ZOOM,
                "lf": "all", "uid": self.settings["user_id"]}  # fmt: skip
        resp = self.fetcher.request("POST", f"{BASE_URL}/getInteractiveLayers", json=body,
                                    headers=self._headers(), timeout=300)  # fmt: skip
        return decode(resp.json())

    def burials_in(self, space_id, schema: str) -> list[dict]:
        body = {"_token": self.settings["csrf"], "components": [{
            "snapshot": self.settings["space_info_snapshot"], "updates": {},
            "calls": [{"path": "", "method": "__dispatch",
                       "params": ["fetchInfo", {"0": str(space_id), "1": None, "2": schema}]}],
        }]}  # fmt: skip
        resp = self.fetcher.request("POST", f"{BASE_URL}/livewire/update", json=body,
                                    headers=self._headers(livewire=True), timeout=120)  # fmt: skip
        comps = resp.json().get("components") or []
        if not comps:
            return []
        return flatten_burials(unwrap(json.loads(comps[0]["snapshot"])["data"].get("burials")))
