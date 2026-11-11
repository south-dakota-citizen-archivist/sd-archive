"""Places for the names records give: a city, town or CDP by name."""

from __future__ import annotations

import sdplaces


def city_place(city: str | None) -> str | None:
    """The fully qualified GEOID of the one South Dakota city, town or census designated place
    with this name; None if there's none, or several (nothing is guessed)."""
    if not city:
        return None
    for kind in ("place", "cdp"):
        try:
            return sdplaces.lookup(kind, city).geoidfq
        except sdplaces.UnknownPlace:
            continue
    return None
