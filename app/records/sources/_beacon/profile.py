"""What a Beacon app shows, found by sampling its report pages (`manage.py beacon_discover`).

Every county sets up its own report: its sections, their labels and columns, and which ones
need an account. A profile records, for one app:

- map: the app and parcel layer IDs, the layer's key field, projection, extent, report URL,
  and the app's other layers
- sections: for each public section, its shape (a {label: value} table, rows, or lines of
  text), how many sampled pages had it, and its fields (labels or columns) with counts
- restricted: the sections that need an account, with counts
- owners: how many pages listed owners, and the kinds of owner seen

Profiles are kept in apps/<App>.json. A run compares each report with its app's profile
(differences()), so a section or field the county adds or drops shows up on the run.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

APPS = Path(__file__).parent / "apps"


def _kind(value) -> str:
    if isinstance(value, dict):
        return "fields"
    if value and isinstance(value[0], dict):
        return "rows"
    return "lines"


def _fields(value) -> list[str]:
    if isinstance(value, dict):
        return list(value)
    if value and isinstance(value[0], dict):
        return list(dict.fromkeys(k for row in value for k in row))
    return []


def map_info(config: dict) -> dict:
    groups = config.get("LayerView") or []
    layers = [{"group": g.get("GroupName"), "name": layer["LayerName"],
               "layer_id": layer.get("LayerId"), "key": layer.get("MapKey")}
              for g in groups for layer in g.get("Layers") or []]  # fmt: skip
    parcel = next((x for x in layers if x["layer_id"] == config.get("LayerId")), {})
    return {"app_id": config.get("AppId"), "layer_id": config.get("LayerId"),
            "layer": parcel.get("name"), "key_field": parcel.get("key"),
            "srid": config.get("SRID"), "units": config.get("Units"),
            "extent": config.get("MaxExtent") or config.get("CurrentExtent"),
            "report_url": re.sub(r"&Q=-?\d+", "", config.get("DefaultReportUrl") or ""),
            "layers": layers}  # fmt: skip


def build(reports: list[dict], config: dict | None = None) -> dict:
    """A profile from parsed report pages (parse.report) and the map configuration."""
    sections: dict[str, dict] = {}
    restricted, owner_types, empty = Counter(), Counter(), Counter()
    with_owners = 0
    for r in reports:
        for title, value in r["sections"].items():
            s = sections.setdefault(title, {"kind": _kind(value), "pages": 0, "fields": Counter()})
            s["pages"] += 1
            s["fields"].update(_fields(value))
        restricted.update(r["restricted"])
        empty.update(r.get("empty") or [])
        with_owners += bool(r["owners"])
        owner_types.update(o.get("type") or "" for o in r["owners"])
    return {
        "map": map_info(config) if config else {},
        "pages": len(reports),
        "sections": {t: {**s, "fields": dict(s["fields"].most_common())}
                     for t, s in sorted(sections.items(), key=lambda x: -x[1]["pages"])},
        "restricted": dict(restricted.most_common()),
        "empty": dict(empty.most_common()),
        "owners": {"pages": with_owners, "types": dict(owner_types.most_common())},
    }  # fmt: skip


def load(app: str) -> dict | None:
    path = APPS / f"{app}.json"
    return json.loads(path.read_text()) if path.exists() else None


def save(app: str, profile: dict) -> Path:
    APPS.mkdir(exist_ok=True)
    path = APPS / f"{app}.json"
    path.write_text(json.dumps(profile, indent=2) + "\n")
    return path


def differences(report: dict, profile: dict) -> list[str]:
    """What a parsed report has that the profile never saw: a section, a field, a section
    that now needs an account. (A section a page lacks isn't one: most are optional.)"""
    out = []
    known = profile.get("sections") or {}
    for title, value in report["sections"].items():
        if title not in known:
            out.append(f"new section: {title}")
            continue
        new = [f for f in _fields(value) if f not in known[title]["fields"]]
        out += [f"new field: {title} / {f}" for f in new]
    for title in report["restricted"]:
        if title in known and title not in (profile.get("restricted") or {}):
            out.append(f"now needs an account: {title}")
    return out
