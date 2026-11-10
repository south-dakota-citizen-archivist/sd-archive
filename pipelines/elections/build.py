"""Build one table of precinct results from everything capture.py saved.

    uv run python pipelines/elections/build.py            # every captured election
    uv run python pipelines/elections/build.py --eid 684

Output (in .cache/elections/build/, not committed):
  precinct_results.parquet  one row per election × race × precinct × candidate or choice
  county_results.parquet    the site's own county totals, where it publishes them
  checks.json               what doesn't add up, for review (also printed)

Each row links to the API request it came from (source_url), and to a results page that
lists the race (page_url). Counties carry their Census GEOID via sdplaces, from the
election system's own county codes: county_geoid as of the election date (2008's Shannon
County is 46113), and county_geoid_current for today's county (46102, Oglala Lakota).

Checks:
  - precinct votes summed by county match the site's county totals (where published)
  - every race found on the pages has precinct rows
  - no duplicate (race, precinct, candidate) rows
  - every county code maps to a county in force on the election date, and the site's county
    name is that county (by today's name, which is what the site shows)
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from datetime import date
from pathlib import Path

import pandas as pd

import sdplaces

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
RAW = REPO / ".cache" / "elections" / "raw"
OUT = REPO / ".cache" / "elections" / "build"
CATALOG = HERE / "data" / "elections.csv"
SITE = "https://electionresults.sd.gov/"
FILE = re.compile(
    r"^(?P<type>[^-]+)-(?P<race>[^-]+)-(?P<osn>[^-]+)-p(?P<party>.*)-(?P<cat>PREC|CTY)-c(?P<county>[^-]+)\.json$"
)


def rows_of(text: str) -> list[dict]:
    data = json.loads(text)
    if isinstance(data, dict):
        data = data.get("d", [])
    if isinstance(data, str):
        data = json.loads(data)
    return data if isinstance(data, list) else []


def county_place(code: str, on: date, issues: list, eid: int, name: str):
    try:
        p = sdplaces.from_id("sos_election_county", code, as_of=on)
    except sdplaces.UnknownPlace as e:
        issues.append({"eid": eid, "check": "county code", "detail": f"{code} ({name}): {e}"})
        return None
    if not p.in_force(on):
        detail = f"code {code} ({name}) → {p.name}, not in force on {on}"
        issues.append({"eid": eid, "check": "county in force", "detail": detail})
    # The site labels old elections with today's county names (2008's Shannon is "Oglala Lakota")
    try:
        same = not name or sdplaces.county(name, current=True).geoid == sdplaces.current(p).geoid
    except sdplaces.UnknownPlace:
        same = False
    if not same:
        issues.append(
            {
                "eid": eid,
                "check": "county name",
                "detail": f"code {code}: site says {name!r}, sdplaces {p.name!r}",
            }
        )
    return p


def build_one(eid: int, info: dict) -> tuple[list[dict], list[dict], list[dict]]:
    root = RAW / str(eid)
    on = date.fromisoformat(info["date"])
    races = {
        (r["type"], r["race"], r["osn"], r["party"] or "0"): r
        for r in json.loads((root / "races.json").read_text())
    }
    manifest = {}
    for line in (root / "manifest.jsonl").read_text().splitlines():
        m = json.loads(line)
        manifest[m["path"]] = m["url"]
    issues: list[dict] = []
    places: dict[str, object] = {}
    precinct, county = [], []

    for path in sorted((root / "api").glob("*.json")):
        m = FILE.match(path.name)
        if not m:
            continue
        race = races.get((m["type"], m["race"], m["osn"], m["party"]))
        page = (
            SITE + race["pages"][0].replace("_", "?", 1).replace("_", "&").removesuffix(".html")
            if race and race["pages"]
            else ""
        )
        url = manifest.get(f"api/{path.name}", "")
        for r in rows_of(path.read_text()):
            code = str(r.get("CountyID") or "")
            if code and code not in places:
                places[code] = county_place(
                    code, on, issues, eid, (r.get("CountyName") or "").strip()
                )
            p = places.get(code)
            row = {
                "eid": eid,
                "election_date": info["date"],
                "election": info["title"],
                "election_status": info["status"],
                "race_type": m["type"],
                "race_id": m["race"],
                "office_seq": m["osn"],
                "party_ballot": "" if m["party"] == "0" else m["party"],
                "race": (r.get("RaceName") or "").strip(),
                "vote_for": r.get("VoteFor"),
                "county_code": code,
                "county": (r.get("CountyName") or "").strip(),
                "county_geoid": p.geoid if p else "",  # as of the election date
                "county_geoid_current": sdplaces.current(p).geoid if p else "",
                "candidate_id": str(r.get("CandidateID") or ""),
                "candidate": " ".join((r.get("calcCandidate") or "").split()),
                "party": (r.get("PartyCode") or "").strip(),
                "party_name": (r.get("PartyName") or "").strip(),
                "ballot_order": r.get("BallotOrder"),
                "votes": int(r.get("calcCandidateVotes") or 0),
                "winner": bool(r.get("Winner")),
                "source_url": url,
                "page_url": page,
            }
            if m["cat"] == "PREC":
                row |= {
                    "precinct_id": str(r.get("StatePrecinctID") or ""),
                    "precinct": " ".join((r.get("PrecinctName") or "").split()),
                    "reported": r.get("IsReported"),
                }
                precinct.append(row)
            else:
                county.append(row)

    # Checks
    summary = (
        json.loads((root / "summary.json").read_text()) if (root / "summary.json").exists() else {}
    )
    if not races:
        cast = summary.get("ballots_cast")
        why = "the site shows 0 ballots cast" if cast == 0 else f"ballots cast: {cast}"
        issues.append({"eid": eid, "check": "no races", "detail": f"no race buttons found ({why})"})
    found = {k for k in races}
    with_rows = {
        (r["race_type"], r["race_id"], r["office_seq"], r["party_ballot"] or "0") for r in precinct
    }
    for k in sorted(found - with_rows):
        issues.append(
            {
                "eid": eid,
                "check": "race without precinct rows",
                "detail": f"{k} {races[k]['pages'][:1]}",
            }
        )
    seen = defaultdict(int)
    for r in precinct:
        seen[
            (
                r["race_type"],
                r["race_id"],
                r["party_ballot"],
                r["county_code"],
                r["precinct_id"],
                r["precinct"],
                r["candidate_id"],
            )
        ] += 1
    dups = [k for k, n in seen.items() if n > 1]
    if dups:
        issues.append(
            {
                "eid": eid,
                "check": "duplicate precinct rows",
                "detail": f"{len(dups)}, e.g. {dups[:3]}",
            }
        )
    sums = defaultdict(int)
    for r in precinct:
        sums[
            (r["race_type"], r["race_id"], r["party_ballot"], r["county_code"], r["candidate_id"])
        ] += r["votes"]
    mismatched = []
    for r in county:
        k = (r["race_type"], r["race_id"], r["party_ballot"], r["county_code"], r["candidate_id"])
        if sums.get(k, 0) != r["votes"]:
            mismatched.append((r["race"], r["county"], r["candidate"], sums.get(k, 0), r["votes"]))
    if mismatched:
        detail = f"{len(mismatched)} of {len(county)} county rows, e.g. {mismatched[:3]}"
        issues.append({"eid": eid, "check": "precinct sums ≠ county totals", "detail": detail})
    return precinct, county, issues


def main() -> None:
    parser = argparse.ArgumentParser(description="Build precinct results from captured raw files")
    parser.add_argument("--eid", type=int, action="append")
    args = parser.parse_args()
    catalog = {int(r["eid"]): r for r in csv.DictReader(CATALOG.open())}
    eids = args.eid or sorted(int(p.name) for p in RAW.iterdir() if (p / "races.json").exists())

    precinct, county, issues = [], [], []
    for eid in eids:
        p, c, i = build_one(eid, catalog[eid])
        precinct += p
        county += c
        issues += i
        n_races = len({(r["race_type"], r["race_id"], r["party_ballot"]) for r in p})
        n_precincts = len({(r["county_code"], r["precinct_id"], r["precinct"]) for r in p})
        info = catalog[eid]
        print(
            f"eid {eid} {info['date']} {info['title']}: {len(p):,} precinct rows, "
            f"{n_races} races, {n_precincts} precincts, {len(i)} issues"
        )

    OUT.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(precinct).to_parquet(OUT / "precinct_results.parquet", index=False)
    pd.DataFrame(county).to_parquet(OUT / "county_results.parquet", index=False)
    (OUT / "checks.json").write_text(json.dumps(issues, indent=1, ensure_ascii=False))
    print(f"\n{len(precinct):,} precinct rows, {len(county):,} county rows → {OUT}")
    for i in issues:
        print(f"  [{i['eid']}] {i['check']}: {i['detail'][:220]}")


if __name__ == "__main__":
    main()
