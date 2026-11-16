"""Build tidy tables of South Dakota motor vehicle titles and registrations from the reports
fetch.py saved.

    uv run python pipelines/motor-vehicles/build.py

Output (in .cache/motor-vehicles/build/, not committed):
  annual.parquet / annual.csv    one row per area × calendar year × measure
  monthly.parquet / monthly.csv  one row per area × month × measure
  checks.json                    what doesn't add up, for review (also printed)

Areas are the state and its 66 counties, each with its Census GEOID (via sdplaces; every
year here is after Shannon County became Oglala Lakota, so that's 46102). Measures are the
report's 14 registration types and titles. Each row names its report, the report's URL and
the page.

Annual figures come from the historical report (the Department's annual totals) for the
years it covers, and from a calendar-year report's Total line for later years (marked
year_to_date until the year's December figures are in). Monthly figures come from the
calendar-year reports; in the current year's report, the months not reported yet (all
zero, at the end) are left out.

Checks:
  - a calendar-year report's months add up to its Total line
  - the counties add up to the state totals
  - every report has the state and all 66 counties
  - a calendar-year report's Total agrees with the historical report for the same year
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import pandas as pd
import parse  # pipelines/motor-vehicles/parse.py (run as a script, so this folder is on the path)

import sdplaces

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
RAW = REPO / ".cache" / "motor-vehicles" / "raw"
BUILD = REPO / ".cache" / "motor-vehicles" / "build"
STATE_GEOID = "0400000US46"
COUNTIES = 66


def geoid(area: str) -> str | None:
    if area == parse.STATE:
        return STATE_GEOID
    try:
        return sdplaces.lookup("county", area).geoidfq
    except sdplaces.UnknownPlace:
        return None


def long_rows(rows: list[dict], report: str, url: str) -> list[dict]:
    out = []
    for r in rows:
        if r["label"].startswith("CY"):
            year, month = int(r["label"].split()[1]), None
        elif r["label"] == "Total":
            year, month = int(report), None
        else:
            year, month = int(report), int(r["label"])
        for m in parse.MEASURES:
            out.append({"geography": "state" if r["area"] == parse.STATE else "county",
                        "area": r["area"], "geoid": geoid(r["area"]), "year": year,
                        "month": month, "measure": m, "value": r[m], "report": report,
                        "source_url": url, "page": r["page"]})  # fmt: skip
    return out


def unreported_months(rows: list[dict]) -> set[int]:
    """The months at the end of a report that are all zero everywhere (not reported yet)."""
    totals = defaultdict(int)
    for r in rows:
        if r["label"].isdigit():
            totals[int(r["label"])] += sum(r[m] for m in parse.MEASURES)
    out = set()
    for month in range(12, 0, -1):
        if totals.get(month, 0):
            break
        out.add(month)
    return out


def build(reports: dict[str, bytes], urls: dict[str, str]) -> tuple[pd.DataFrame, pd.DataFrame,
                                                                    dict]:  # fmt: skip
    checks: dict[str, list] = defaultdict(list)
    monthly, totals, historical = [], [], []
    latest = max(int(n) for n in reports if n != "historical")
    through = {}
    for name, pdf in sorted(reports.items()):
        rows = parse.read(pdf)
        areas = {r["area"] for r in rows}
        if len(areas - {parse.STATE}) != COUNTIES or parse.STATE not in areas:
            checks["areas missing"].append({"report": name, "areas": len(areas)})
        for area in sorted(areas):
            if geoid(area) is None:
                checks["unknown counties"].append({"report": name, "area": area})
        rows_long = long_rows(rows, name, urls.get(name, ""))
        if name == "historical":
            historical += rows_long
            continue
        skip = unreported_months(rows) if int(name) == latest else set()
        through[int(name)] = 12 - len(skip)
        monthly += [r for r in rows_long if r["month"] is not None and r["month"] not in skip]
        totals += [r for r in rows_long if r["month"] is None]

    monthly_df = pd.DataFrame(monthly)
    totals_df = pd.DataFrame(totals)
    hist_df = pd.DataFrame(historical)

    # months add up to the Total line
    summed = monthly_df.groupby(["report", "area", "measure"])["value"].sum()
    for (report, area, measure), total in totals_df.set_index(
            ["report", "area", "measure"])["value"].items():  # fmt: skip
        if summed.get((report, area, measure), 0) != total:
            checks["months don't add up to the Total"].append(
                {"report": report, "area": area, "measure": measure, "total": int(total),
                 "months": int(summed.get((report, area, measure), 0))})  # fmt: skip

    # counties add up to the state
    for label, df in (("annual (historical)", hist_df), ("calendar-year Totals", totals_df),
                      ("months", monthly_df)):  # fmt: skip
        keys = ["report", "year", "month", "measure"]
        df = df.assign(month=df.month.fillna(0).astype(int))  # 0: a whole year
        counties = df[df.geography == "county"].groupby(keys)["value"].sum()
        state = df[df.geography == "state"].set_index(keys)["value"]
        for key, value in state.items():
            if counties.get(key, 0) != value:
                found = dict(zip(keys, key, strict=True))
                checks["counties don't add up to the state"].append(
                    {"table": label, **found, "month": found["month"] or None,
                     "counties": int(counties.get(key, 0)), "state": int(value)})  # fmt: skip

    # the calendar-year reports' Totals against the historical report
    hist = hist_df.set_index(["year", "area", "measure"])["value"]
    for r in totals_df.itertuples():
        if (r.year, r.area, r.measure) in hist.index and hist[
            (r.year, r.area, r.measure)
        ] != r.value:
            checks["calendar-year Total ≠ historical"].append(
                {"year": r.year, "area": r.area, "measure": r.measure,
                 "calendar_year_report": int(r.value),
                 "historical": int(hist[(r.year, r.area, r.measure)])})  # fmt: skip

    covered = set(hist_df.year)
    later = totals_df[~totals_df.year.isin(covered)].copy()
    annual = pd.concat([hist_df, later], ignore_index=True).drop(columns=["month"])
    annual["year_to_date"] = annual.year.map(lambda y: through.get(y, 12) < 12 and y not in covered)
    annual["through_month"] = annual.year.map(
        lambda y: None if y in covered else through.get(y)).astype("Int64")  # fmt: skip
    annual = annual.sort_values(["year", "geography", "area", "measure"],
                                ascending=[False, False, True, True])  # fmt: skip
    monthly_df = monthly_df.sort_values(["year", "month", "geography", "area", "measure"],
                                        ascending=[False, False, False, True, True])  # fmt: skip
    return annual.reset_index(drop=True), monthly_df.reset_index(drop=True), dict(checks)


def main() -> None:
    urls = json.loads((RAW / "reports.json").read_text())
    reports = {p.stem: p.read_bytes() for p in sorted((RAW / "reports").glob("*.pdf"))}
    annual, monthly, checks = build(reports, urls)
    BUILD.mkdir(parents=True, exist_ok=True)
    for name, df in (("annual", annual), ("monthly", monthly)):
        df.to_parquet(BUILD / f"{name}.parquet", index=False)
        df.to_csv(BUILD / f"{name}.csv", index=False)
    (BUILD / "checks.json").write_text(json.dumps(checks, indent=1, default=str))
    print(f"annual: {len(annual):,} rows ({annual.year.min()}–{annual.year.max()}); "
          f"monthly: {len(monthly):,} rows")  # fmt: skip
    for name, found in checks.items():
        print(f"  {name}: {len(found)}")
        for item in found[:5]:
            print(f"    {item}")


if __name__ == "__main__":
    main()
