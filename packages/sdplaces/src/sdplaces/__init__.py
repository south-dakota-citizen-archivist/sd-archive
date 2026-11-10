"""South Dakota places keyed by Census GEOID: counties past and present, cities and towns,
Census designated places and tribal areas. See DESIGN.md.

    import sdplaces
    sdplaces.county("Lawrence").geoid                 # '46081'
    sdplaces.county("Shannon", current=True).name     # 'Oglala Lakota County'
    sdplaces.place("Spearfish").parent                # '46081'
    sdplaces.from_id("sos_election_county", 57).name # 'Spink County'
"""

from ._pandas import add_geoid
from ._places import (
    KINDS,
    STATE,
    AmbiguousPlace,
    NotInForce,
    Place,
    UnknownPlace,
    all_places,
    children,
    counties,
    county,
    current,
    from_id,
    get,
    house_district,
    lookup,
    place,
    school_district,
    senate_district,
    tribal,
)

__all__ = [
    "KINDS",
    "STATE",
    "AmbiguousPlace",
    "NotInForce",
    "Place",
    "UnknownPlace",
    "add_geoid",
    "all_places",
    "children",
    "counties",
    "county",
    "current",
    "from_id",
    "get",
    "house_district",
    "lookup",
    "place",
    "school_district",
    "senate_district",
    "tribal",
]
