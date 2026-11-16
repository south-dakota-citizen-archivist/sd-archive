"""Helping review GIS candidates: what kind of publisher a candidate looks like, and a scan of
its services directory (every service and layer, nothing saved to the census)."""

from __future__ import annotations

import re
from functools import cache
from urllib.parse import urlparse

SD_GOVERNMENT = "South Dakota government"
NAMED = "names a South Dakota place or agency"
GOVERNMENT = "other government"
OTHER = "other"
ORDER = {SD_GOVERNMENT: 0, NAMED: 1, GOVERNMENT: 2, OTHER: 3}
# South Dakota agencies' names as they show up in hosts, organizations and owners: inside a
# word ("SoDakGFP", "BIT.GIS.Dev") for these, as a whole word for the short ones ("DANR", not
# "USDANRCS")
AGENCIES = {"southdakota": "South Dakota", "sodak": "South Dakota", "sdgov": "the state",
            "sdgis": "the state's GIS", "bitgis": "BIT (the state's IT bureau)",
            "sddot": "DOT", "sdgfp": "Game, Fish and Parks", "sdsmt": "School of Mines",
            "sdbor": "the Board of Regents"}  # fmt: skip
AGENCY_WORDS = {
    "danr": "DANR",
    "denr": "DENR",
    "gfp": "Game, Fish and Parks",
    "sdgs": "the Geological Survey",
    "sdsu": "SDSU",
    "sdsmt": "School of Mines",
}


def _flat(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


@cache
def _place_patterns() -> list[tuple[str, str]]:
    """(flattened pattern, what it names): "lawrencecounty", "cityofspearfish", "cityoftea",
    "spearfishsd", "siouxfalls"… A place's name alone or with "sd" counts when it's eight
    letters or more ("siouxfalls"); shorter ones only after "city of", "town of" or "city"."""
    import sdplaces

    out = []
    for c in sdplaces.counties(include_historical=True):
        name = _flat(c.name.removesuffix(" County"))
        out += [(name + "county", c.name), (name + "cosd", c.name)]
    for p in sdplaces.all_places("place", include_historical=False):
        name = re.sub(r" (city|town)$", "", p.name)
        flat = _flat(name)
        out += [("cityof" + flat, name), ("townof" + flat, name), ("city" + flat, name)]
        if len(flat) >= 8:
            out += [(flat, name)]
        elif len(flat) >= 5:
            out += [(flat + "sd", name)]
    for d in sdplaces.all_places("school_district", include_historical=False):
        name = re.sub(r" (School District|SD)( \d+-\d+)?$", "", d.name)
        if len(_flat(name)) >= 4:
            out += [(_flat(name) + "schooldistrict", d.name)]
    for t in sdplaces.all_places("tribal", include_historical=False):
        out += [(_flat(t.name.split(" Reservation")[0]) + "sioux", t.name)]
    return sorted(set(out), key=lambda x: -len(x[0]))


def _names(text: str) -> str | None:
    flat = _flat(text)
    for pattern, place in _place_patterns():
        if pattern in flat:
            return place
    for pattern, agency in AGENCIES.items():
        if pattern in flat:
            return agency
    words = set(re.findall(r"[a-z0-9]+", (text or "").lower()))
    return next((agency for word, agency in AGENCY_WORDS.items() if word in words), None)


def classify(candidate) -> tuple[str, str]:
    """(kind, why): a South Dakota government's web domain; a host, organization or owner
    naming a South Dakota county, city, school district, tribe or state agency; another
    government's domain; or other."""
    host = urlparse(candidate.url).hostname or ""
    # who publishes, not what: nearly every item's title names somewhere in South Dakota
    texts = [host, candidate.title, *owners(candidate)]
    named = next(((text, found) for text in texts if (found := _names(text))), None)
    if re.search(r"\.(gov|us|mil)$", host):
        if re.search(r"(^|\.)sd\.(gov|us)$", host) or named:
            why = f"{host} is a government domain" + (
                f", and “{named[0]}” names {named[1]}" if named else "")  # fmt: skip
            return SD_GOVERNMENT, why
        return GOVERNMENT, f"{host} is a government domain"
    if named:
        return NAMED, f"“{named[0]}” names {named[1]}"
    return OTHER, ""


# how recently a candidate's data changed, as a signal within its publisher group
CURRENT, AGING, IDLE, UNDATED = "updated this past year", "1–3 years old", "over 3 years old", ""
RECENCY_ORDER = {CURRENT: 0, AGING: 1, UNDATED: 1, IDLE: 2}


def last_updated(candidate) -> tuple[str | None, str]:
    """(date, what it is): the latest edit to any layer's data, once scanned (the server's
    own record of when the data last changed); otherwise the latest modified date of the
    South Dakota items that point at the directory."""
    edited = [x["edited_at"][:10] for x in (candidate.scan or {}).get("layers", [])
              if x.get("edited_at")]  # fmt: skip
    if edited:
        return max(edited), "data last edited"
    latest = (candidate.evidence.get("latest") or {}).get("modified")
    return (latest, "an item last modified") if latest else (None, "")


def recency(date: str | None, today=None) -> str:
    from datetime import date as Date

    if not date:
        return UNDATED
    today = today or Date.today()
    days = (today - Date.fromisoformat(date[:10])).days
    return CURRENT if days <= 366 else AGING if days <= 3 * 366 else IDLE


def owners(candidate) -> list[str]:
    found = []
    for examples in (candidate.evidence.get("examples") or {}).values():
        for e in examples:
            if e.get("owner") and e["owner"] not in found:
                found.append(e["owner"])
    return found


def summarize(scan) -> dict:
    """A directory scan (sdgis.arcgis.directory.DirectoryScan) as JSON to keep with the
    candidate."""
    layers = []
    for layer in scan.layers:
        layers.append({
            "url": layer.url, "service": layer.service, "service_type": layer.service_type,
            "folder": layer.folder, "name": layer.name, "kind": layer.kind,
            "geometry_type": (layer.geometry_type or "").removeprefix("esriGeometry"),
            "count": layer.count, "description": (layer.description or "")[:500],
            "fields": [{k: f.get(k, "") for k in ("name", "type", "alias")}
                       for f in layer.fields],
            "edited_at": layer.edited_at.isoformat() if layer.edited_at else None,
            "error": layer.error,
        })  # fmt: skip
    return {
        "services": [{"name": s.name, "type": s.type, "url": s.url, "folder": s.folder,
                      "error": s.error} for s in scan.services],
        "layers": layers,
        "folder_errors": scan.folder_errors,
        "timed_out": scan.timed_out,
        "features": sum(x["count"] or 0 for x in layers),
    }  # fmt: skip


def scan(candidate, time_limit: float = 300) -> dict:
    """The directory's services and layers (a server behind a bot check is asked as Chrome:
    sdgis.arcgis.client.make_session)."""
    from sdgis.arcgis import directory

    return summarize(directory.scan(candidate.url, counts=True, workers=2,
                                    time_limit=time_limit))  # fmt: skip


def layer_belongs(candidate, url: str) -> bool:
    """A layer URL inside the candidate's directory (the sample view fetches only these)."""
    return url.startswith(candidate.url.rstrip("/") + "/") and bool(
        re.search(r"/(MapServer|FeatureServer)/\d+$", url)
    )
