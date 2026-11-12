"""Build tidy tables of South Dakota voter registration by county and party from the
snapshots.

    uv run python pipelines/voter-registration/build.py

Output (in .cache/voter-registration/build/, not committed):
  registration.parquet / .csv             one row per snapshot date × county × party (as
                                          reported: every party, and inactive voters)
  registration-simplified.parquet / .csv  active voters only, by Republican, Democratic and
                                          other; Washabaugh County (merged into Jackson in
                                          1983) left out, and Shannon County's rows under its
                                          current name, Oglala Lakota (46102)
  chart.json                              the simplified table by date, statewide and by
                                          county, for the published page
  checks.json                             what doesn't look right, for review (also printed)

Each row has the county's Census GEOID as of the snapshot's date (via sdplaces: Shannon is
46113 until 2015), the election the snapshot is for (if any), its source (the report on
the Secretary of State's site; for 1976–2014, the archived copy of the election history page
it was entered from) and the archived copy of the report (the voter-registration-sources
release).
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import date
from functools import cache
from pathlib import Path

import pandas as pd

import sdplaces

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
BUILD = REPO / ".cache" / "voter-registration" / "build"
ARCHIVE = ("https://github.com/south-dakota-citizen-archivist/sd-archive/releases/download/"
           "voter-registration-sources/{date}.pdf")  # fmt: skip
SHANNON, OGLALA_LAKOTA, WASHABAUGH = "0500000US46113", "0500000US46102", "0500000US46131"
MAJOR = ("republican", "democratic")


@cache
def county_geoid(name: str, on: str) -> str | None:
    try:
        return sdplaces.lookup("county", name, as_of=on).geoidfq
    except (sdplaces.UnknownPlace, sdplaces.AmbiguousPlace):
        return None


def snapshots(folder: Path) -> list[dict]:
    """Every snapshot row as reported: date, county, and a count per column."""
    out = []
    for path in sorted(folder.glob("*.csv")):
        with path.open(newline="") as f:
            for row in csv.DictReader(f):
                if row["date"] != path.stem:
                    raise ValueError(f"{path.name}: a row dated {row['date']}")
                out.append(row)
    return out


def sources(path: Path) -> dict[str, dict]:
    """Report date → its source (the first link to it)."""
    out = {}
    for url, s in json.loads(path.read_text()).items():
        out.setdefault(s["report_date"], {"url": url, **s})
    return out


def build(rows: list[dict], by_date: dict[str, dict], elections: dict[str, str]
          ) -> tuple[dict[str, pd.DataFrame], dict, dict]:  # fmt: skip
    checks: dict[str, list] = defaultdict(list)
    long = []
    for r in rows:
        d, county = r["date"], r["county"]
        geoid = county_geoid(county, d)
        if geoid is None:
            checks["counties without a GEOID"].append({"date": d, "county": county})
        src = by_date.get(d)
        for party, value in r.items():
            if party in ("date", "county") or not value or int(value) <= 0:
                continue
            long.append({"date": d, "county": county, "county_geoid": geoid, "party": party,
                         "voters": int(value), "election": elections.get(d),
                         "source_url": src["url"] if src else ARCHIVE.format(date=d),
                         "archived_copy": ARCHIVE.format(date=d),
                         "read": (src.get("read", "parsed") if src else
                                  "from the election history")})  # fmt: skip
    df = pd.DataFrame(long).sort_values(["date", "county", "voters"],
                                        ascending=[True, True, False])  # fmt: skip
    for d, g in df.groupby("date"):
        n = g.county.nunique()
        # Washabaugh County, until it merged into Jackson in 1983, is listed on its own in
        # some reports and with Jackson ("Jackson/Washabaugh", under Jackson here) in others
        expected = {66, 67} if d < "1983-01-01" else {66}
        if n not in expected:
            checks["snapshots without every county"].append({"date": d, "counties": n})
    since = set(by_date) - set(df.date)
    for d in sorted(since):
        checks["sources without a snapshot"].append({"date": d})
    for d in sorted(set(elections) - set(df.date)):
        checks["elections without a snapshot"].append({"date": d, "election": elections[d]})

    simple = df[(df.party != "inactive") & (df.county_geoid != WASHABAUGH)].copy()
    simple["party"] = simple.party.where(simple.party.isin(MAJOR), "other")
    simple.loc[simple.county_geoid == SHANNON, "county"] = "Oglala Lakota"
    simple.loc[simple.county_geoid == SHANNON, "county_geoid"] = OGLALA_LAKOTA
    keys = [
        "date",
        "county",
        "county_geoid",
        "party",
        "election",
        "source_url",
        "archived_copy",
        "read",
    ]
    simple = simple.groupby(keys, dropna=False, as_index=False).voters.sum()
    simple = simple.sort_values(["date", "county", "voters"], ascending=[True, True, False])
    simple = simple[["date", "county", "county_geoid", "party", "voters", "election",
                     "source_url", "archived_copy", "read"]]  # fmt: skip
    return ({"registration": df.reset_index(drop=True),
             "registration-simplified": simple.reset_index(drop=True)},
            chart(simple, by_date), dict(checks))  # fmt: skip


PARTIES = ("republican", "democratic", "other")


def chart(simple: pd.DataFrame, by_date: dict[str, dict]) -> dict:
    """The page's data: the dates, each date's source and election, and the voters by party
    statewide and in each county (null where a county has no figure that date)."""
    dates = sorted(simple.date.unique())
    wide = simple.pivot_table(index=["county_geoid", "date"], columns="party", values="voters",
                              aggfunc="sum")  # fmt: skip
    state = simple.pivot_table(index="date", columns="party", values="voters", aggfunc="sum")

    def series(frame, party):
        col = frame[party] if party in frame else pd.Series(dtype=float)
        return [None if pd.isna(v) else int(v) for v in col.reindex(dates)]

    names = simple.drop_duplicates("county_geoid").set_index("county_geoid").county
    elections = simple.drop_duplicates("date").set_index("date").election
    return {
        "updated": date.today().isoformat(),
        "dates": dates,
        "sources": [by_date[d]["url"] if d in by_date else ARCHIVE.format(date=d) for d in dates],
        "archived": [ARCHIVE.format(date=d) for d in dates],
        "elections": [None if pd.isna(elections.get(d)) else elections.get(d) for d in dates],
        "parties": list(PARTIES),
        "state": {p: series(state, p) for p in PARTIES},
        "counties": {
            g: {"name": names[g], **{p: series(wide.loc[g], p) for p in PARTIES}}
            for g in sorted(names.index)
        },  # fmt: skip
    }


def main() -> None:
    rows = snapshots(HERE / "snapshots")
    tables, data, checks = build(
        rows, sources(HERE / "sources.json"), json.loads((HERE / "elections.json").read_text())
    )
    BUILD.mkdir(parents=True, exist_ok=True)
    for name, df in tables.items():
        df.to_parquet(BUILD / f"{name}.parquet", index=False)
        df.to_csv(BUILD / f"{name}.csv", index=False)
        print(f"{name}: {len(df):,} rows ({df.date.min()} to {df.date.max()}, "
              f"{df.date.nunique()} snapshots)")  # fmt: skip
    (BUILD / "chart.json").write_text(json.dumps(data, separators=(",", ":")))
    (BUILD / "checks.json").write_text(json.dumps(checks, indent=1, default=str))
    for name, found in checks.items():
        print(f"  {name}: {len(found)}")
        for item in found[:6]:
            print(f"    {item}")


if __name__ == "__main__":
    main()
