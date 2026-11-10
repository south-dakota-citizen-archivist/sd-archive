"""Rebuild src/sdplaces/data/places.csv from Census cartographic boundary files.

    uv run python packages/sdplaces/scripts/build_data.py [--year 2024]

Current places come from the Census files (the same ones the GIS hub loads, so GEOIDs
match). Historical places come from data/historical.csv, which is edited by hand with a
source for each row. Run this deliberately (yearly, or when the Census changes something),
and review the diff before committing. Needs geopandas (the `geo` extra) and the network.
"""

from __future__ import annotations

import argparse
import csv
import re
import unicodedata
from pathlib import Path

import geopandas as gpd

STATE_FIPS = "46"
DATA = Path(__file__).resolve().parents[1] / "src" / "sdplaces" / "data"
CDP_LSAD = "57"  # Census designated place: a statistical area, not a government
EQUAL_AREA_CRS = 5070  # CONUS Albers
MIN_TRIBAL_OVERLAP_M2 = 500_000  # land in SD, not just touching the border (Santee, Neb.)

COLUMNS = [
    "kind",
    "geoid",
    "geoidfq",
    "name",
    "short_name",
    "slug",
    "parent",
    "lat",
    "lon",
    "ocd_id",
    "valid_from",
    "valid_to",
    "successor",
    "source",
]


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def read(base: str, name: str) -> gpd.GeoDataFrame:
    return gpd.read_file(f"/vsizip//vsicurl/{base}/{name}").to_crs(4326)


def point(geom) -> tuple[str, str]:
    p = geom.representative_point()
    return f"{p.y:.5f}", f"{p.x:.5f}"


def build(year: int) -> list[dict]:
    base = f"https://www2.census.gov/geo/tiger/GENZ{year}/shp"
    source = f"Census cartographic boundary files {year}"
    rows: list[dict] = []

    def add(kind, r, name, short, slug, parent, geom):
        lat, lon = point(geom)
        rows.append(
            {
                "kind": kind,
                "geoid": r.GEOID,
                "geoidfq": r.GEOIDFQ,
                "name": name,
                "short_name": short,
                "slug": slug,
                "parent": parent,
                "lat": lat,
                "lon": lon,
                "source": source,
            }
        )

    states = read(base, f"cb_{year}_us_state_500k.zip")
    sd = states[states.STATEFP == STATE_FIPS].iloc[0]
    add("state", sd, sd.NAME, sd.NAME, slugify(sd.NAME), "", sd.geometry)

    counties = read(base, f"cb_{year}_us_county_500k.zip")
    counties = counties[counties.STATEFP == STATE_FIPS]
    for r in counties.itertuples():
        add("county", r, r.NAMELSAD, r.NAME, slugify(r.NAMELSAD), STATE_FIPS, r.geometry)

    # A city's parent is the county containing a point inside it (as the GIS hub does)
    places = read(base, f"cb_{year}_{STATE_FIPS}_place_500k.zip")
    reps = places.copy()
    reps["geometry"] = places.geometry.representative_point()
    joined = gpd.sjoin(reps, counties[["GEOID", "geometry"]], predicate="within", how="left")
    county_of = dict(zip(joined.GEOID_left, joined.GEOID_right, strict=True))
    for r in places.itertuples():
        kind = "cdp" if r.LSAD == CDP_LSAD else "place"
        slug = slugify(r.NAME) if kind == "place" else slugify(f"{r.NAME} cdp")
        add(kind, r, r.NAMELSAD, r.NAME, slug, county_of.get(r.GEOID) or "", r.geometry)

    tribal = read(base, f"cb_{year}_us_aiannh_500k.zip")
    overlap = tribal.intersection(sd.geometry).to_crs(EQUAL_AREA_CRS).area
    tribal = tribal[overlap > MIN_TRIBAL_OVERLAP_M2]
    for r in tribal.itertuples():
        add("tribal", r, r.NAMELSAD, r.NAME, slugify(r.NAMELSAD), STATE_FIPS, r.geometry)

    with (DATA / "historical.csv").open(newline="") as f:
        for h in csv.DictReader(f):
            rows.append({**h, "slug": slugify(h["name"]), "lat": "", "lon": ""})

    # A renamed place's new code starts when the old one ended. (A merged place's successor
    # already existed, so its start date doesn't change.)
    for h in [r for r in rows if r.get("change") == "renamed"]:
        for r in rows:
            if r["kind"] == h["kind"] and r["geoid"] == h["successor"]:
                r["valid_from"] = h["valid_to"]

    order = {"state": 0, "county": 1, "place": 2, "cdp": 3, "tribal": 4}
    rows.sort(key=lambda r: (order[r["kind"]], r["name"]))
    return [{c: r.get(c, "") for c in COLUMNS} for r in rows]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--year", type=int, default=2024)
    args = parser.parse_args()
    rows = build(args.year)
    out = DATA / "places.csv"
    with out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["kind"]] = counts.get(r["kind"], 0) + 1
    print(f"wrote {out} ({len(rows)} rows): {counts}")


if __name__ == "__main__":
    main()
