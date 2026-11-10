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
import io
import json
import os
import re
import unicodedata
import urllib.request
from pathlib import Path

import geopandas as gpd

from sdfetch import BROWSER_HEADERS

STATE_FIPS = "46"
DATA = Path(__file__).resolve().parents[1] / "src" / "sdplaces" / "data"
OCD_CSV = "https://raw.githubusercontent.com/opencivicdata/ocd-division-ids/master/identifiers/country-us.csv"
# NCES Common Core of Data district directory, via the Urban Institute's Education Data API
CCD = "https://educationdata.urban.org/api/v1/school-districts/ccd/directory/{year}/?fips=46"
CCD_REGULAR = 1  # agency_type 1: regular school districts (not cooperatives or state schools)
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
    # GDAL fetches /vsicurl/ itself, and would otherwise send "GDAL/x.y"
    os.environ["GDAL_HTTP_USERAGENT"] = BROWSER_HEADERS["User-Agent"]
    return gpd.read_file(f"/vsizip//vsicurl/{base}/{name}").to_crs(4326)


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers=BROWSER_HEADERS)
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


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

    # School districts: South Dakota has only unified districts. GEOID = NCES LEAID.
    districts = read(base, f"cb_{year}_{STATE_FIPS}_unsd_500k.zip")
    reps = districts.copy()
    reps["geometry"] = districts.geometry.representative_point()
    joined = gpd.sjoin(reps, counties[["GEOID", "geometry"]], predicate="within", how="left")
    county_of = dict(zip(joined.GEOID_left, joined.GEOID_right, strict=True))
    for r in districts.itertuples():
        short = re.sub(r"\s+School District\b", "", r.NAME).strip()
        add(
            "school_district",
            r,
            r.NAME,
            short,
            slugify(r.NAME),
            county_of.get(r.GEOID) or "",
            r.geometry,
        )

    # State legislative districts: 35 senate; 37 house (26 and 28 split into A and B)
    for kind, chamber in (("sldu", "Senate"), ("sldl", "House")):
        for r in read(base, f"cb_{year}_{STATE_FIPS}_{kind}_500k.zip").itertuples():
            slug = slugify(f"{chamber} district {r.NAME}")
            add(kind, r, f"State {chamber} District {r.NAME}", r.NAME, slug, STATE_FIPS, r.geometry)

    with (DATA / "historical.csv").open(newline="") as f:
        for h in csv.DictReader(f):
            rows.append({**h, "slug": slugify(h["name"]), "lat": "", "lon": ""})

    # A renamed place's new code starts when the old one ended, as does a district created by a
    # merger ("merged_new"). Plain "merged": the successor already existed, so its start stays.
    for h in [r for r in rows if r.get("change") in ("renamed", "merged_new")]:
        for r in rows:
            if r["kind"] == h["kind"] and r["geoid"] == h["successor"]:
                r["valid_from"] = h["valid_to"]

    add_ocd_ids(rows)
    order = {
        k: i
        for i, k in enumerate(
            ["state", "county", "place", "cdp", "tribal", "school_district", "sldu", "sldl"]
        )
    }
    rows.sort(key=lambda r: (order[r["kind"]], r["name"]))
    return [{c: r.get(c, "") for c in COLUMNS} for r in rows]


def add_ocd_ids(rows: list[dict]) -> None:
    """Open Civic Data division IDs. Counties are matched by name; everything else by the
    Census GEOID OCD records. OCD's own county GEOIDs aren't trusted: its Oglala Lakota
    County row carries Shannon County's retired code (46113)."""
    sd = [
        r
        for r in csv.DictReader(io.StringIO(fetch(OCD_CSV).decode()))
        if r["id"].startswith("ocd-division/country:us/state:sd")
    ]
    ids = {r["id"] for r in sd}
    by_geoid: dict[tuple[str, str], str] = {}
    for r in sd:
        tail = r["id"].rsplit("/", 1)[-1].split(":")[0]
        for col in ("census_geoid", "census_geoid_14", "census_geoid_12"):
            g = r.get(col, "")
            if g:
                g = g.split("-", 1)[-1]  # "place-4660020", "sldu-46001"
                kind = {
                    "place": "place",
                    "school_district": "school_district",
                    "sldu": "sldu",
                    "sldl": "sldl",
                }.get(tail)
                if kind and r["id"].count("/") <= 4:
                    by_geoid.setdefault((kind, g), r["id"])
    for row in rows:
        if row["kind"] == "state":
            row["ocd_id"] = "ocd-division/country:us/state:sd"
        elif row["kind"] == "county":
            name = re.sub(r"[^a-z0-9]+", "_", row["short_name"].lower()).strip("_")
            candidate = f"ocd-division/country:us/state:sd/county:{name}"
            row["ocd_id"] = candidate if candidate in ids else ""
        elif (row["kind"], row["geoid"]) in by_geoid:
            row["ocd_id"] = by_geoid[(row["kind"], row["geoid"])]


def doe_crosswalk(year: int = 2022, older: tuple[int, ...] = (2016,)) -> list[dict]:
    """DOE district numbers ("40002") → school district GEOIDs (NCES LEAIDs), from the CCD's
    state_leaid ("SD-40002"). Regular districts only; older directories add retired districts."""
    rows: dict[str, dict] = {}
    for y in (year, *older):
        for r in json.loads(fetch(CCD.format(year=y)))["results"]:
            if r.get("agency_type") != CCD_REGULAR or not (r.get("state_leaid") or "").startswith(
                "SD-"
            ):
                continue
            number = r["state_leaid"].removeprefix("SD-")
            rows.setdefault(
                number,
                {
                    "system_id": number,
                    "geoid": r["leaid"],
                    "name_in_source": r["lea_name"],
                    "evidence": (
                        f"NCES Common Core of Data district directory {y} "
                        f"(state_leaid SD-{number}, leaid {r['leaid']}), "
                        "via the Urban Institute Education Data API"
                    ),
                    "note": "",
                },
            )
    return sorted(rows.values(), key=lambda r: r["system_id"])


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
    missing_ocd = [
        f"{r['kind']} {r['name']}"
        for r in rows
        if not r["ocd_id"] and r["kind"] not in ("cdp", "tribal")
    ]
    print(f"OCD IDs: {sum(1 for r in rows if r['ocd_id'])} rows; none for: {missing_ocd or 'none'}")

    xwalk = doe_crosswalk()
    geoids = {(r["kind"], r["geoid"]) for r in rows}
    unknown = [x for x in xwalk if ("school_district", x["geoid"]) not in geoids]
    if unknown:
        raise SystemExit(f"DOE crosswalk rows with no school district in places.csv: {unknown}")
    path = DATA / "crosswalks" / "doe_district.csv"
    with path.open("w", newline="") as f:
        w = csv.DictWriter(
            f, fieldnames=["system_id", "geoid", "name_in_source", "evidence", "note"]
        )
        w.writeheader()
        w.writerows(xwalk)
    print(f"wrote {path} ({len(xwalk)} districts)")


if __name__ == "__main__":
    main()
