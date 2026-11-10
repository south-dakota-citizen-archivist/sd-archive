"""Spot the same layer published in several services (e.g. a county's map service, WFS, WMS
and autocomplete services all exposing its parcels), so a crawl keeps only the original.

A copy can also be a narrower view: a map viewer's 45-column parcels over the same 16,909
parcels as the full 120-column layer."""

import re

from platforms.arcgis.directory import LayerInfo

# Services that usually re-publish another service's layers for a specific client
SECONDARY_SERVICE_RE = re.compile(r"autocomplete|top10|wfs|wms|secure|_old|test|copy|view", re.I)
# Fields that aren't identical must still overlap this much: a one-field boundary layer has
# OBJECTID like everything else, but that doesn't make it a copy of anything
MIN_SHARED_FIELDS = 10
# A copy may lack this share of the original's fields, for a renamed ID column and the like
MISSING_FIELDS_ALLOWED = 0.05


def covers(original: frozenset[str], copy: frozenset[str]) -> bool:
    """True if the copy's fields match the original's, or are nearly all among them."""
    if copy == original:
        return True
    return (
        len(copy & original) >= MIN_SHARED_FIELDS
        and len(copy - original) <= len(copy) * MISSING_FIELDS_ALLOWED
    )


def find_copies(
    layers: list[LayerInfo],
    places: dict[str, int | None],
    keep: set[str] = frozenset(),
    age: dict[str, int] | None = None,
) -> dict[str, str]:
    """Map each copy's URL to its original's URL.

    Layers are compared within a place (URL -> jurisdiction id), kind, geometry type and
    feature count. Layers in `keep` (watched or harvested) are never copies. Otherwise the
    original is the one with the most fields, then on a main service, then a FeatureServer,
    then the oldest (`age`: URL -> existing dataset id).
    """
    age = age or {}

    def preference(layer: LayerInfo) -> tuple:
        return (
            layer.url not in keep,
            -len(layer.signature),
            bool(SECONDARY_SERVICE_RE.search(layer.service)),
            layer.service_type != "FeatureServer",
            age.get(layer.url, float("inf")),
            layer.url,
        )

    groups: dict[tuple, list[LayerInfo]] = {}
    for layer in layers:
        # Empty or uncounted layers can share a schema without being the same data
        if not layer.count:
            continue
        key = (places.get(layer.url), layer.kind, layer.geometry_type, layer.count)
        groups.setdefault(key, []).append(layer)

    copy_of = {}
    for group in groups.values():
        originals: list[LayerInfo] = []
        for layer in sorted(group, key=preference):
            # No fields means the metadata didn't load, not that it's a copy of anything
            original = (
                layer.signature
                and layer.url not in keep
                and next((o for o in originals if covers(o.signature, layer.signature)), None)
            )
            if original:
                copy_of[layer.url] = original.url
            else:
                originals.append(layer)
    return copy_of
