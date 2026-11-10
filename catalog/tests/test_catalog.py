"""The dataset catalog: every entry complete for its grain (its unit of analysis)."""

import tomllib
from collections import Counter
from pathlib import Path

import pytest

CATALOG = Path(__file__).resolve().parents[1] / "datasets.toml"
DATASETS = tomllib.loads(CATALOG.read_text())["dataset"]

GRAINS = {"aggregate", "records"}
GEOGRAPHIES = {
    "state",
    "county",
    "place",
    "cdp",
    "tribal",
    "school_district",
    "sldl",
    "sldu",
    "precinct",
}
ENTITIES = {"person", "organization", "government", "property"}
PLACE_LINKS = {
    "address", "county", "city", "geometry", "jurisdiction", "legislative district", "none",
}  # fmt: skip
COMMON = {"id", "name", "project", "grain", "coverage", "source"}
AGGREGATE = {"geography", "period", "measures", "dimensions", "suppression"}
RECORDS = {"unit", "entities", "place_link"}
KNOWN = COMMON | AGGREGATE | RECORDS | {"notes"}


@pytest.mark.parametrize("d", DATASETS, ids=lambda d: d["id"])
def test_coverage_is_places_in_force(d):
    """coverage lists fully qualified GEOIDs of places in force today: no typos, no retired
    codes (0500000US46113), no plain GEOIDs (46019 is a county and two districts)."""
    import sdplaces

    assert d["coverage"] and len(set(d["coverage"])) == len(d["coverage"])
    for geoid in d["coverage"]:
        assert "US" in geoid, f"{geoid}: use the fully qualified GEOID"
        place = sdplaces.get(geoid)
        assert sdplaces.current(place) == place, f"{geoid} ({place.name}) is retired"


def test_ids_are_unique():
    assert [k for k, n in Counter(d["id"] for d in DATASETS).items() if n > 1] == []


@pytest.mark.parametrize("d", DATASETS, ids=lambda d: d["id"])
def test_entry_is_complete_for_its_grain(d):
    assert d.keys() >= COMMON, COMMON - d.keys()
    assert d.keys() <= KNOWN, d.keys() - KNOWN
    assert d["grain"] in GRAINS
    if d["grain"] == "aggregate":
        # the place is the unit: which places, which periods, what's measured
        assert {"geography", "period", "measures"} <= d.keys()
        assert d["geography"] and set(d["geography"]) <= GEOGRAPHIES
        assert not d.keys() & RECORDS
    else:
        # things within a place are the unit: what a row is, what it names, how it's placed
        assert d.keys() >= RECORDS, RECORDS - d.keys()
        assert d["entities"] and set(d["entities"]) <= ENTITIES
        assert d["place_link"] in PLACE_LINKS
        assert not d.keys() & AGGREGATE
