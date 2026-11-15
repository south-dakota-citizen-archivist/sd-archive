"""South Dakota's census blocks with their redistricting counts (PL 94-171), 2000, 2010
and 2020, from the Census Bureau's API.

For each census, each county in force on census day and each table of the redistricting
file, the API's answer for every block is saved as served, then all of a year's tables are
joined into one row per block:

- 2020: P1 (race), P2 (Hispanic or Latino, by race), P3 and P4 (the same, 18 and over),
  P5 (group quarters), H1 (housing units, occupied and vacant)
- 2010: P1 to P4, H1
- 2000: PL001 to PL004 (the 2000 redistricting file has no housing table), and H001
  (housing units) from Summary File 1

    CENSUS_API_KEY=… uv run python pipelines/census_blocks/fetch.py [--years 2020,2010,2000]

Output
  .cache/census_blocks/raw/<year>/<table>/<county>.json   every answer (not committed)
  pipelines/census_blocks/data/blocks_<year>.parquet       one row per block: the block's
                                                           GEOID, then every count
  pipelines/census_blocks/data/variables_<year>.csv        each count's table and label

Rerunning reuses saved answers. The key is sent with each request but never saved.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import time
from datetime import date
from pathlib import Path

import requests

import sdfetch
import sdplaces

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
RAW = REPO / ".cache" / "census_blocks" / "raw"
DATA = HERE / "data"
API = "https://api.census.gov/data/{year}/{dataset}"
TABLES = {
    2020: [("dec/pl", t) for t in ("P1", "P2", "P3", "P4", "P5", "H1")],
    2010: [("dec/pl", t) for t in ("P1", "P2", "P3", "P4", "H1")],
    2000: [("dec/pl", t) for t in ("PL001", "PL002", "PL003", "PL004")] + [("dec/sf1", "H001")],
}
PAUSE = 1.0  # seconds between requests


def counties(year: int) -> list[str]:
    """The three-digit codes of the counties in force on census day (April 1)."""
    return sorted(p.geoid[2:] for p in sdplaces.counties(as_of=date(year, 4, 1)))


def fetch(store: sdfetch.Store, year: int, dataset: str, table: str, county: str) -> list[list]:
    name = f"{year}/{table}/{county}.json"
    if store.has(name):
        return json.loads(store.read(name))
    params = {"get": f"group({table})", "for": "block:*", "in": f"state:46 county:{county} tract:*"}
    url = API.format(year=year, dataset=dataset)
    for attempt in range(4):
        r = requests.get(url, params=params | {"key": os.environ["CENSUS_API_KEY"]},
                         headers=sdfetch.BROWSER_HEADERS, timeout=300)  # fmt: skip
        if r.status_code == 200:
            break
        time.sleep(10 * (attempt + 1))
    r.raise_for_status()
    shown = requests.Request("GET", url, params=params).prepare().url  # without the key
    store.save(name, r.content, shown)
    time.sleep(PAUSE)
    return r.json()


def variables(year: int, dataset: str, table: str) -> dict[str, str]:
    r = requests.get(f"{API.format(year=year, dataset=dataset)}/groups/{table}.json",
                     headers=sdfetch.BROWSER_HEADERS, timeout=60)  # fmt: skip
    r.raise_for_status()
    return {k: v["label"] for k, v in r.json()["variables"].items()}


def block_id(row: dict, year: int) -> str:
    tract = row["tract"].ljust(6, "0") if year == 2000 and len(row["tract"]) == 4 else row["tract"]
    return f"46{row['county']}{tract}{row['block']}"


def build(year: int, store: sdfetch.Store) -> Path:
    import pandas as pd

    labels, blocks = {}, {}
    for dataset, table in TABLES[year]:
        names = variables(year, dataset, table)
        # the counts (2020 also lists an annotation column for each, ending in NA)
        counts = sorted(k for k in names if k not in ("GEO_ID", "NAME") and not k.endswith("NA"))
        for k in counts:
            labels[k] = (table, names[k])
        for county in counties(year):
            rows = fetch(store, year, dataset, table, county)
            head = rows[0]
            for values in rows[1:]:
                row = dict(zip(head, values, strict=True))
                b = blocks.setdefault(block_id(row, year), {})
                for k in counts:
                    if k in row:
                        b[k] = int(row[k]) if row[k] not in (None, "") else None
        print(f"  {year} {table}: {len(blocks)} blocks so far")
    DATA.mkdir(parents=True, exist_ok=True)
    out = DATA / f"blocks_{year}.parquet"
    frame = pd.DataFrame([{"block": k, **v} for k, v in sorted(blocks.items())])
    frame[frame.columns[1:]] = frame[frame.columns[1:]].astype("Int64")
    frame.to_parquet(out, index=False)
    with (DATA / f"variables_{year}.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["variable", "table", "label"])
        for k, (table, label) in sorted(labels.items()):
            w.writerow([k, table, label])
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", default="2020,2010,2000")
    args = ap.parse_args()
    if not os.environ.get("CENSUS_API_KEY"):
        raise SystemExit("CENSUS_API_KEY isn't set")
    store = sdfetch.Store(RAW)
    for year in [int(y) for y in args.years.split(",")]:
        out = build(year, store)
        print(f"{year}: {out}")


if __name__ == "__main__":
    main()
