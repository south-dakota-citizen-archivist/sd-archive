"""sdplaces' legislative district plans from their block files (plan_blocks.py, amend_plan.py).

Writes, for each plan, data/plans/<plan>.csv.gz (every block of that census and its senatorial
and house district, with how each was placed), and data/districts.csv: one place per district
of each earlier plan, in force from the first primary election held under it until the next
plan's, with its population at that census and a point inside it. The current plan's districts
are the Census-coded places in places.csv; a district a later version left unchanged stays one
place (the court redrew only 2001's Districts 21, 26 and 27).

    uv run python scripts/build_plans.py PLAN_DIR
"""

from __future__ import annotations

import argparse
import csv
import gzip
import re
from collections import defaultdict
from pathlib import Path

import geopandas as gpd
import pandas as pd

DATA = Path(__file__).resolve().parents[1] / "src" / "sdplaces" / "data"
CACHE = Path("~/.cache/sdplaces").expanduser()
BLOCK_SHAPES = {2000: CACHE / "blocks/2000/tl_2010_46_tabblock00.zip",
                2010: CACHE / "blocks/2010/tl_2010_46_tabblock10.zip",
                2020: CACHE / "blocks/2020/tl_2020_46_tabblock20.zip"}  # fmt: skip
POPULATION = Path(__file__).resolve().parents[3] / "pipelines" / "census_blocks" / "data"

# plan → (its census, its block file, in force from, until, where it comes from, what it changes)
PLANS = {
    "2001": (
        2000,
        "plan-2001-blocks.csv",
        "2002-06-04",
        "2012-06-05",
        "2001 First Special Session, SB 1 (SDCL 2-2-32 to 2-2-40)",
        None,
    ),
    "2005": (
        2000,
        "plan-2001-court-blocks.csv",
        "2006-06-06",
        "2012-06-05",
        "Bone Shirt v. Hazeltine, 387 F. Supp. 2d 1035 (D.S.D. 2005), Remedial Plan 1 "
        "(Districts 21, 26, 27), per Census's 2010 district files",
        {"21", "26", "27"},
    ),
    "2011": (
        2010,
        "plan-2011-blocks.csv",
        "2012-06-05",
        "2022-06-07",
        "2011 Special Session, HB 1001 (SDCL 2-2-41 to 2-2-51)",
        None,
    ),
    "2021": (
        2020,
        "plan-2021-blocks.csv",
        "2022-06-07",
        "",
        "2021 First Special Session, HB 1001 (SDCL 2-2-41 to 2-2-51, amended)",
        None,
    ),
}
CURRENT = "2021"
FIELDS = ["kind", "geoid", "geoidfq", "name", "short_name", "slug", "parent", "lat", "lon",
          "ocd_id", "valid_from", "valid_to", "successor", "source", "plan", "census",
          "people"]  # fmt: skip
PREFIX = {"sldu": "610U900US", "sldl": "620L900US"}


def senatorial(n: str) -> str:
    return re.match(r"\d+", n)[0]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("plans", type=Path, help="the folder with each plan's block CSV")
    args = ap.parse_args()
    (DATA / "plans").mkdir(exist_ok=True)
    rows = []
    for plan, (census, name, start, end, source, changes) in PLANS.items():
        blocks = list(csv.DictReader((args.plans / name).open()))
        with gzip.open(DATA / "plans" / f"{plan}.csv.gz", "wt", newline="") as f:
            w = csv.DictWriter(
                f, fieldnames=["block", "senate", "senate_from", "house", "house_from"]
            )  # noqa: E501
            w.writeheader()
            w.writerows(blocks)
        if plan == CURRENT:
            continue  # its districts are places.csv's
        counts = pd.read_parquet(POPULATION / f"blocks_{census}.parquet")
        total = next(c for c in ("P1_001N", "P001001", "PL001001") if c in counts.columns)
        people = dict(zip(counts["block"], counts[total].fillna(0).astype(int), strict=True))
        shapes = gpd.read_file(f"zip://{BLOCK_SHAPES[census]}")
        gid = [c for c in shapes.columns if c.startswith(("GEOID", "BLKIDFP"))][0]
        shapes = shapes[[gid, "geometry"]].rename(columns={gid: "block"}).to_crs(5070)
        for kind, column in (("sldu", "senate"), ("sldl", "house")):
            district = {b["block"]: b[column] for b in blocks}
            shapes["d"] = shapes["block"].map(district)
            areas = shapes.dropna(subset=["d"]).dissolve(by="d")
            pop = defaultdict(int)
            for b, n in district.items():
                pop[n] += people.get(b, 0)
            for n, geom in areas.geometry.items():
                if changes and senatorial(n) not in changes:
                    continue  # the court left it as the 2001 plan drew it
                inside = gpd.GeoSeries([geom.representative_point()], crs=5070).to_crs(4326)[0]
                chamber = "Senate" if kind == "sldu" else "House"
                geoid = f"46{n.zfill(3)}-{plan}"  # Census's code ("46015", "4626A") and the plan
                until = end
                if plan == "2001" and senatorial(n) in PLANS["2005"][5]:
                    until = PLANS["2005"][2]  # redrawn by the court for 2006
                rows.append({
                    "kind": kind, "geoid": geoid, "geoidfq": PREFIX[kind] + geoid,
                    "name": f"State {chamber} District {n} ({plan} plan)", "short_name": n,
                    "slug": f"{chamber.lower()}-district-{n.lower()}-{plan}-plan", "parent": "46",
                    "lat": f"{inside.y:.5f}", "lon": f"{inside.x:.5f}", "ocd_id": "",
                    "valid_from": start, "valid_to": until, "successor": "", "source": source,
                    "plan": plan, "census": census, "people": pop[n],
                })  # fmt: skip
        print(f"{plan}: {len(blocks)} blocks")
    with (DATA / "districts.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} district places → {DATA / 'districts.csv'}")


if __name__ == "__main__":
    main()
