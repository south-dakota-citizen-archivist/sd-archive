"""Build tidy tables of municipal sales tax by city, month and SIC industry from the
workbooks fetch.py saved.

    uv run python pipelines/sales-tax/build.py

Output (in .cache/sales-tax/build/, not committed):
  city_sic.parquet         every line of every month's report (parse.py's levels), one row per
                           month × city × tax × line
  city_taxes.parquet/.csv  each city's taxable sales and tax due by month and tax (General,
                           Gross Receipts), and its tax due for all taxes
  checks.json              what doesn't add up, for review (also printed)

Each row has the city's GEOID where exactly one South Dakota city or town has its name, and
names its file and spreadsheet row. "N/A" amounts (fewer than three licenses) stay empty,
with `suppressed` true.

Checks (only where nothing in the sum is suppressed):
  - a major group's lines add up to its total
  - a division's major groups add up to its total
  - a tax's recap divisions add up to the tax's total
  - a city's taxes add up to its total tax due, and so do its recap's divisions
  - which cities have no place GEOID; which months have fewer cities than the month before
"""

from __future__ import annotations

import json
from collections import defaultdict
from functools import cache
from pathlib import Path

import pandas as pd
import parse  # pipelines/sales-tax/parse.py (run as a script, so this folder is on the path)

import sdplaces

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
RAW = REPO / ".cache" / "sales-tax" / "raw"
BUILD = REPO / ".cache" / "sales-tax" / "build"
CENTS = 0.011


@cache
def place_geoid(name: str | None) -> str | None:
    if not name:
        return None
    try:
        return sdplaces.lookup("place", name).geoidfq
    except (sdplaces.UnknownPlace, sdplaces.AmbiguousPlace):
        return None


def _sum_check(parts: pd.DataFrame, total: pd.DataFrame, keys: list[str], label: str,
               checks: dict, columns=("taxable", "tax_due")) -> None:  # fmt: skip
    """Total rows against their parts summed (allowing a cent of rounding per part), where
    no part is suppressed."""
    grouped = parts.groupby(keys, dropna=False)
    summed = grouped[list(columns)].sum(min_count=1).add_suffix("_parts")
    summed["_suppressed"] = grouped.suppressed.any()
    summed["_parts"] = grouped.size()
    joined = total.set_index(keys).join(summed, how="inner")
    joined = joined[~joined._suppressed]
    for col in columns:
        diff = (joined[col] - joined[f"{col}_parts"]).abs()
        off = joined[diff > CENTS * joined._parts]
        for key, row in off.iterrows():
            checks[label].append({**dict(zip(keys, key, strict=True)), "column": col,
                                  "total": row[col], "parts": round(row[f"{col}_parts"], 2),
                                  "file": row.file, "row": row.row})  # fmt: skip


def build(months: dict[str, str], read) -> tuple[dict[str, pd.DataFrame], dict]:
    checks: dict[str, list] = defaultdict(list)
    rows = []
    for month, url in sorted(months.items()):
        found = parse.read(read(month), month, f"{month}.xlsx")
        for r in found:
            r["source_url"] = url
        rows += found
    df = pd.DataFrame(rows)
    df["city_geoid"] = df.city.map(place_geoid)

    def check(level_parts, level_total, keys, label):
        _sum_check(df[df.level.isin(level_parts)], df[df.level == level_total], keys, label, checks)

    # rows of one section share month, city, tax; within it, division and major group
    base = ["month", "city", "tax"]
    check(
        ["industry", "other"],
        "major group",
        [*base, "division", "major_group"],
        "a major group's lines ≠ its total",
    )
    check(["major group"], "division", [*base, "division"], "a division's major groups ≠ its total")
    check(["recap division"], "tax total", base, "a tax's recap divisions ≠ its total")
    totals = df[df.level == "city total"]
    _sum_check(
        df[df.level == "tax total"],
        totals,
        ["month", "city"],
        "a city's taxes ≠ its total tax due",
        checks,
        columns=("tax_due",),
    )
    _sum_check(
        df[df.level == "city recap division"],
        totals,
        ["month", "city"],
        "a city's recap divisions ≠ its total tax due",
        checks,
        columns=("tax_due",),
    )
    for city in sorted(df[df.city_geoid.isna()].city.unique()):
        checks["cities without a place GEOID"].append(
            {"city": city, "months": int(df[df.city == city].month.nunique())}
        )
    per_month = df.groupby("month").city.nunique()
    for (_, n0), (m1, n1) in zip(per_month.items(), list(per_month.items())[1:], strict=False):
        if n1 < n0:
            checks["months with fewer cities than the month before"].append(
                {"month": m1, "cities": int(n1), "before": int(n0)}
            )

    city_taxes = df[df.level.isin(["tax total", "city total"])].assign(
        tax=lambda d: d.tax.fillna("all"))[
        ["month", "city", "city_geoid", "tax", "taxable", "tax_due", "file", "row",
         "source_url"]].reset_index(drop=True)  # fmt: skip
    cols = [
        "month",
        "city",
        "city_geoid",
        "tax",
        "level",
        "division",
        "major_group",
        "code",
        "description",
        "taxable",
        "tax_due",
        "suppressed",
        "file",
        "row",
        "source_url",
    ]
    return {"city_sic": df[cols], "city_taxes": city_taxes}, dict(checks)


@cache
def county_geoid(name: str | None) -> str | None:
    if not name:
        return None
    try:
        return sdplaces.lookup("county", name).geoidfq
    except (sdplaces.UnknownPlace, sdplaces.AmbiguousPlace):
        return None


TOURISM_RATE = 0.015  # the tourism tax, 1.5% of taxable sales


def build_tourism(months: dict[str, str], read) -> tuple[pd.DataFrame, dict]:
    checks: dict[str, list] = defaultdict(list)
    rows = []
    for month, url in sorted(months.items()):
        found, unread = parse.tourism(read(month), month, f"{month}.pdf")
        rows += [{**r, "source_url": url} for r in found]
        checks["tourism lines not read"] += unread
    df = pd.DataFrame(rows)
    df["county_geoid"] = df.county.map(county_geoid)
    for county in sorted(df[df.county.notna() & df.county_geoid.isna()].county.unique()):
        checks["tourism counties without a GEOID"].append({"county": county})
    lines = df[df.level != "state total"]
    states = df[df.level == "state total"].set_index("month")
    for month in sorted(set(df.month)):
        if month not in states.index:
            checks["tourism months without a state total"].append({"month": month})
            continue
        disclosed = lines[lines.month == month][["taxable", "tax_due"]].sum()
        state = states.loc[month]
        if disclosed.taxable > state.taxable + CENTS * len(lines):
            checks["tourism counties add up to more than the state"].append(
                {"month": month, "counties": round(disclosed.taxable, 2), "state": state.taxable}
            )
    rated = lines.dropna(subset=["taxable", "tax_due"])
    off = rated[(rated.tax_due - rated.taxable * TOURISM_RATE).abs()
                > 0.01 + rated.taxable.abs() * 1e-4]  # fmt: skip
    for r in off.itertuples():
        checks["tourism tax due isn't 1.5% of taxable sales"].append(
            {"month": r.month, "county": r.county, "code": r.code, "taxable": r.taxable,
             "tax_due": r.tax_due, "page": r.page})  # fmt: skip
    cols = [
        "month",
        "county",
        "county_geoid",
        "level",
        "code",
        "description",
        "taxable",
        "tax_due",
        "suppressed",
        "file",
        "page",
        "source_url",
    ]
    return df[cols], dict(checks)


def build_city_tax_due(months: dict[str, str], read,
                       city_taxes: pd.DataFrame | None) -> tuple[pd.DataFrame, dict]:  # fmt: skip
    checks: dict[str, list] = defaultdict(list)
    rows = []
    for month, url in sorted(months.items()):
        found, unread = parse.city_tax_due(read(month), month, f"{month}.pdf")
        rows += [{**r, "source_url": url} for r in found]
        checks["city tax due lines not read"] += unread
    df = pd.DataFrame(rows)
    df["city_geoid"] = df.city.map(place_geoid)
    # the ten largest cities are in a table repeated at the top of each page (and not in
    # the alphabetical list): keep one copy, after checking the copies agree
    largest = df[df.listing == "largest"]
    for (month, city), g in largest.groupby(["month", "city"]):
        if g[["tax_due", "tax_due_year_before"]].nunique(dropna=False).max() > 1:
            checks["city tax due: the largest cities' table differs between pages"].append(
                {"month": month, "city": city, "tax_due": g.tax_due.tolist()}
            )
    df = pd.concat([largest.drop_duplicates(["month", "city"]), df[df.listing == "all"]],
                   ignore_index=True).sort_values(["month", "city"])  # fmt: skip
    dup = df[df.duplicated(["month", "city"], keep=False)]
    for (month, city), _ in dup.groupby(["month", "city"]):
        checks["city tax due: a city twice in a month"].append({"month": month, "city": city})
    # the year before's figure against that month's own report: a revision since
    once = df.drop_duplicates(["month", "city"], keep=False)
    earlier = once[["month", "city", "tax_due"]].assign(
        month=lambda d: (d.month.str[:4].astype(int) + 1).astype(str) + d.month.str[4:]
    )
    joined = once.dropna(subset=["tax_due_year_before"]).merge(
        earlier, on=["month", "city"], suffixes=("", "_reported_then")
    )
    different = joined[(joined.tax_due_year_before - joined.tax_due_reported_then).abs() > CENTS]
    checks["city tax due: the year before's figure against that month's report"].append(
        {
            "compared": len(joined),
            "different": len(different),
            "examples": different.head(5)[
                ["month", "city", "tax_due_year_before", "tax_due_reported_then"]
            ].to_dict("records"),
        }
    )
    if city_taxes is not None:
        totals = city_taxes[city_taxes.tax == "all"].set_index(["month", "city"]).tax_due
        joined = df.join(
            totals[~totals.index.duplicated(keep=False)].rename("workbook"),
            on=["month", "city"],
            how="inner",
        )
        differ = joined[(joined.tax_due - joined.workbook).abs() > CENTS]
        checks["city tax due against the SIC workbook's city total"].append(
            {"compared": len(joined), "different": len(differ),
             "examples": differ.head(5)[["month", "city", "tax_due", "workbook"]
                                        ].to_dict("records")})  # fmt: skip
    cols = [
        "month",
        "city",
        "city_geoid",
        "tax_due",
        "tax_due_year_before",
        "percent_change",
        "listing",
        "file",
        "page",
        "source_url",
    ]
    return df[cols], dict(checks)


def main() -> None:
    months = json.loads((RAW / "municipal-by-sic.json").read_text())
    tables, checks = build(months, lambda m: (RAW / "municipal-by-sic" / f"{m}.xlsx").read_bytes())
    for kind, fn in (("tourism", build_tourism), ("city-tax-due", build_city_tax_due)):
        listed = json.loads((RAW / f"{kind}.json").read_text())

        def read(m, kind=kind):
            return (RAW / kind / f"{m}.pdf").read_bytes()

        if kind == "tourism":
            table, more = fn(listed, read)
        else:
            table, more = fn(listed, read, tables["city_taxes"])
        tables[kind.replace("-", "_")] = table
        checks.update(more)
    BUILD.mkdir(parents=True, exist_ok=True)
    for name, df in tables.items():
        df.to_parquet(BUILD / f"{name}.parquet", index=False)
        print(f"{name}: {len(df):,} rows ({df.month.min()} to {df.month.max()})")
    for name in ("city_taxes", "tourism", "city_tax_due"):
        tables[name].to_csv(BUILD / f"{name}.csv", index=False)
    (BUILD / "checks.json").write_text(json.dumps(checks, indent=1, default=str))
    for name, found in checks.items():
        print(f"  {name}: {len(found)}")
        for item in found[:4]:
            print(f"    {item}")


if __name__ == "__main__":
    main()
