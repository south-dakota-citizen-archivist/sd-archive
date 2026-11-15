"""A plan as changed for some of its districts, from a district file that has the change.

The 2001 plan's Districts 21, 26 and 27 were redrawn by the federal court in Bone Shirt v.
Hazeltine (2005, for the 2006 elections on): the court's plan isn't in the acts. Census's
district files from after it (2008, 2010) agree with each other on those districts. This
takes a plan's blocks (plan_blocks.py's CSV) and, for every block that the plan or the file
puts in one of the changed districts, uses the file's district, marked as coming from it.

    uv run python scripts/amend_plan.py PLAN.csv BLOCKS.zip SLDU.zip SLDL.zip OUT.csv \\
        --districts 21,26,27 --source "Bone Shirt v. Hazeltine (2005), per Census's 2010 file"
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import Counter
from pathlib import Path

import geopandas as gpd

sys.path.insert(0, str(Path(__file__).parent))
from plan_blocks import district_of  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    for a in ("plan", "blocks", "sldu", "sldl", "out"):
        ap.add_argument(a, type=Path)
    ap.add_argument("--districts", required=True, help="senatorial districts that changed")
    ap.add_argument("--source", required=True, help="where the change comes from")
    args = ap.parse_args()
    changed = set(args.districts.split(","))

    shapes = gpd.read_file(f"zip://{args.blocks}")
    gid = [c for c in shapes.columns if c.startswith(("GEOID", "BLKIDFP"))][0]
    lat = [c for c in shapes.columns if c.startswith("INTPTLAT")][0]
    lon = [c for c in shapes.columns if c.startswith("INTPTLON")][0]
    points = gpd.GeoDataFrame({"block": shapes[gid]}, crs=shapes.crs,
                              geometry=gpd.points_from_xy(shapes[lon].astype(float),
                                                          shapes[lat].astype(float)))  # fmt: skip
    senate, house = district_of(points, args.sldu), district_of(points, args.sldl)

    def senatorial(n: str) -> str:
        return re.match(r"\d+", n)[0] if n else ""

    rows = list(csv.DictReader(args.plan.open()))
    moved = Counter()
    for r in rows:
        b = r["block"]
        if senatorial(r["senate"]) in changed or senatorial(senate.get(b, "")) in changed:
            if senate.get(b):
                if senate[b] != r["senate"]:
                    moved[(r["senate"], senate[b])] += 1
                r["senate"], r["senate_from"] = senate[b], args.source
            if house.get(b):
                r["house"], r["house_from"] = house[b], args.source
    with args.out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"{sum(moved.values())} blocks moved: {moved.most_common()} → {args.out}")


if __name__ == "__main__":
    main()
