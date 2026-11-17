"""South Dakota's population by county and year: the Census Bureau's July 1 estimates (the
Population Estimates Program), from its API, kept in data/population.csv for pages that show
rates per resident.

    CENSUS_API_KEY=… uv run python pipelines/population/fetch.py

- 2010–2019: the vintage 2019 estimates (`2019/pep/population`, July 1 of each year).
- 2020 on: the latest vintage the API has (`<vintage>/pep/charv`, MONTH 7), which revises
  2020 onward. Later vintages are published as files before they reach the API; rerun this
  when a newer one does (VINTAGES).

Output: data/population.csv, one row per area × year: geography (state or county), area,
geoid (fully qualified Census GEOID), year, population, vintage, source_url (the API query,
without the key). The table is committed; nothing reads the API at build time. The key is
sent with each request but never saved.
"""

from __future__ import annotations

import csv
import os
from pathlib import Path

import requests

import sdfetch

HERE = Path(__file__).resolve().parent
OUT = HERE / "data" / "population.csv"
API = "https://api.census.gov/data/"
STATE = "46"
VINTAGES = [  # (dataset, the variables asked for, how to read a row's year)
    ("2019/pep/population", "NAME,POP,DATE_CODE,DATE_DESC", "date_desc"),
    ("2023/pep/charv", "NAME,POP,YEAR,MONTH", "month"),
]


def _get(dataset: str, fields: str, geo: dict) -> list[dict]:
    params = {"get": fields, **geo}
    r = requests.get(API + dataset, params=params | {"key": os.environ["CENSUS_API_KEY"]},
                     headers=sdfetch.BROWSER_HEADERS, timeout=120)  # fmt: skip
    r.raise_for_status()
    head, *rows = r.json()
    shown = requests.Request("GET", API + dataset, params=params).prepare().url
    return [dict(zip(head, row, strict=True)) | {"_url": shown} for row in rows]


def _year(row: dict, how: str) -> int | None:
    """The year of a July 1 estimate; None for the census counts and estimate bases."""
    if how == "date_desc":
        desc = row["DATE_DESC"]
        return int(desc.split()[0].split("/")[2]) if desc.startswith("7/1/") else None
    return int(row["YEAR"]) if row["MONTH"] == "7" else None


def rows() -> list[dict]:
    found: dict[tuple[str, int], dict] = {}
    for dataset, fields, how in VINTAGES:
        vintage = int(dataset.split("/")[0])
        areas = (
            ("state", {"for": f"state:{STATE}"}),
            ("county", {"for": "county:*", "in": f"state:{STATE}"}),
        )
        for geography, geo in areas:
            for r in _get(dataset, fields, geo):
                year = _year(r, how)
                if year is None:
                    continue
                code = f"0400000US{STATE}" if geography == "state" else \
                    f"0500000US{STATE}{r['county']}"  # fmt: skip
                # a later vintage revises the years it covers
                found[(code, year)] = {
                    "geography": geography, "area": r["NAME"].removesuffix(", South Dakota"),
                    "geoid": code, "year": year, "population": int(r["POP"]),
                    "vintage": vintage, "source_url": r["_url"]}  # fmt: skip
    return sorted(found.values(), key=lambda r: (r["year"], r["geography"] != "state", r["geoid"]))


def main() -> None:
    if not os.environ.get("CENSUS_API_KEY"):
        raise SystemExit("CENSUS_API_KEY isn't set")
    out = rows()
    years = sorted({r["year"] for r in out})
    for year in years:  # the counties add up to the state
        state = sum(r["population"] for r in out if r["year"] == year and r["geography"] == "state")
        counties = sum(r["population"] for r in out
                       if r["year"] == year and r["geography"] == "county")  # fmt: skip
        n = sum(1 for r in out if r["year"] == year and r["geography"] == "county")
        if state != counties or n != 66:
            raise SystemExit(f"{year}: {n} counties summing to {counties:,}, state {state:,}")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0]))
        w.writeheader()
        w.writerows(out)
    print(f"{OUT}: {len(out)} rows, {years[0]}–{years[-1]}")


if __name__ == "__main__":
    main()
