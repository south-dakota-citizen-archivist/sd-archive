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

import exports  # pipelines/elections/exports.py (run as a script, so this folder is on the path)
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


def _norm(name: str) -> str:
    """For matching candidate names across the API and exports ('O"Dea' = "O'Dea")."""
    name = re.sub(r"[\"'’‘`]", "'", str(name))
    return " ".join(name.split()).casefold()


def _party(code) -> str:
    """A primary ballot's party for matching: the nonpartisan ballot is NON, "" or 0."""
    return "" if code in (None, "", "0", "NON") else code


def race_type(e: dict, summary_types: dict, counties_of: dict) -> str:
    """The type of a race the API doesn't list: from the statewide and legislative workbooks;
    failing that, legislative by its title (2008 has no legislative workbook); failing that, a
    race in only one county's workbook is a county race."""
    if t := summary_types.get(e["race_id"]):
        return t
    if e["race_title"].startswith(("State Senator", "State Representative")):
        return "LEG"
    return "CTY" if len(counties_of[e["race_id"]]) == 1 else ""


def merge_exports(eid, on, root, races, precinct, county) -> tuple[list, list, list, set]:
    """Check the API's precinct rows against the export workbooks, and fill what it lacks.

    - Check: each export TOTALS row (race, county, candidate) against the API's precinct votes
      summed the same way, where the API has that race in that county.
    - Fill: races and counties the API has no precinct rows for (2012; a few ballot questions;
      local races) come from the exports, with source="export". Race and candidate details
      (type, IDs, party) come from the API's own county rows when it has them.
    - Replace: where the API's precinct rows for a race in a county are all zero but its county
      total isn't, and the export's totals equal the API's county totals, the export's precincts
      are used instead (2012 primary, Minnehaha). Returns those (race, party, county) keys so
      the caller can drop the zero rows.
    - Recounts: recount sheets, row for row like the fill, returned separately.
    """
    repeats: list = []
    rows = exports.read_election(root, repeats)
    if not rows:
        return [], [], [], set()
    issues: list[dict] = []
    if repeats:
        detail = f"{len(repeats)} repeated sheets with different rows, e.g. {repeats[:3]}"
        issues.append({"eid": eid, "check": "export repeated sheets", "detail": detail})
    summary_types = exports.race_types(root)
    counties_of = defaultdict(set)
    for e in rows:
        counties_of[e["race_id"]].add(e["county"])
    api_pairs = {(r["race_id"], _party(r["party_ballot"]), r["county"]) for r in precinct}

    sums: dict[tuple, int] = defaultdict(int)
    for r in precinct:
        sums[(r["race_id"], _party(r["party_ballot"]), r["county"], _norm(r["candidate"]))] += r[
            "votes"
        ]
    api_total, county_total, export_total = defaultdict(int), defaultdict(int), defaultdict(int)
    for r in precinct:
        api_total[(r["race_id"], _party(r["party_ballot"]), r["county"])] += r["votes"]
    for r in county:
        county_total[(r["race_id"], _party(r["party_ballot"]), r["county"])] += r["votes"]
    for e in rows:
        e["pair"] = (e["race_id"], _party(e["party_ballot"]), e["county"])
        if e["precinct"] is None and not e["recount"]:
            export_total[e["pair"]] += e["votes"] or 0
    zeroed = {
        k
        for k in api_pairs
        if api_total[k] == 0 and county_total[k] > 0 and export_total.get(k) == county_total[k]
    }
    if zeroed:
        detail = f"{len(zeroed)} races × counties, used the exports' precincts: {sorted(zeroed)}"
        issues.append({"eid": eid, "check": "API precinct rows all zero", "detail": detail})
    checked, mismatched = 0, []
    for e in rows:
        if (
            e["precinct"] is None
            and not e["recount"]
            and e["pair"] in api_pairs
            and e["pair"] not in zeroed
        ):
            checked += 1
            k = (*e["pair"], _norm(e["candidate"]))
            if sums.get(k) != e["votes"]:
                mismatched.append(
                    (e["race_title"], e["county"], e["candidate"], sums.get(k), e["votes"])
                )
    if mismatched:
        detail = f"{len(mismatched)} of {checked} export totals, e.g. {mismatched[:3]}"
        issues.append({"eid": eid, "check": "API precinct sums ≠ export totals", "detail": detail})

    # What the API knows about each race and candidate, from any of its rows
    race_info: dict[str, dict] = {}
    cands: dict[tuple, dict] = {}
    for r in county + precinct:
        race_info.setdefault((r["race_id"], _party(r["party_ballot"])), r)
        cands.setdefault((r["race_id"], _party(r["party_ballot"]), _norm(r["candidate"])), r)
    by_race_id = defaultdict(list)
    for k in races:
        by_race_id[(k[1], _party(k[3]))].append(k)
    # County codes and GEOIDs by today's name, which is what the site and its exports show
    counties: dict[str, tuple] = {}
    for code in range(1, 68):
        try:
            p = sdplaces.from_id("sos_election_county", code, as_of=on)
        except sdplaces.UnknownPlace:
            continue
        today = sdplaces.current(p)
        counties[today.short_name] = (str(code).zfill(2), p.geoid, today.geoid)
    url = f"{SITE}ResultsExport.aspx?eid={eid}"
    filled, recounts, unknown = [], [], set()
    for e in rows:
        if e["precinct"] is None or (not e["recount"] and e["pair"] in api_pairs - zeroed):
            continue
        race_key = e["pair"][:2]
        race = race_info.get(race_key, {})
        keys = by_race_id.get(race_key, [])
        rtype, _, osn, party = keys[0] if len(keys) == 1 else ("", "", "", e["party_ballot"])
        rtype = race.get("race_type") or rtype or race_type(e, summary_types, counties_of)
        cand = cands.get((*race_key, _norm(e["candidate"])), {})
        code, geoid, geoid_now = counties.get(e["county"], ("", "", ""))
        if not code:
            unknown.add(e["county"])
        (recounts if e["recount"] else filled).append(
            {
                "eid": eid,
                "election_date": race.get("election_date") or on.isoformat(),
                "election": race.get("election", ""),
                "election_status": race.get("election_status", ""),
                "race_type": rtype,
                "race_id": e["race_id"],
                "office_seq": race.get("office_seq") or osn,
                "party_ballot": race.get("party_ballot", "" if party in ("0", "NON") else party),
                "race": race.get("race") or e["race_title"],
                "vote_for": race.get("vote_for"),
                "county_code": code,
                "county": e["county"],
                "county_geoid": geoid,
                "county_geoid_current": geoid_now,
                "candidate_id": cand.get("candidate_id", ""),
                "candidate": cand.get("candidate") or e["candidate"],
                "party": cand.get("party", ""),
                "party_name": cand.get("party_name", ""),
                "ballot_order": cand.get("ballot_order"),
                "votes": e["votes"] or 0,
                "winner": cand.get("winner", False),
                "source_url": url,
                "page_url": race.get("page_url", ""),
                "precinct_id": "",
                "precinct": e["precinct"],
                "reported": None,
                "source": "export_recount" if e["recount"] else "export",
                "precinct_seq": e["precinct_seq"],
                "export_file": f"exports/{e['file']}",
                "export_sheet": e["sheet"],
            }
        )
    if unknown:
        issues.append(
            {"eid": eid, "check": "export county", "detail": f"unknown: {sorted(unknown)}"}
        )
    return filled, recounts, issues, zeroed


def build_one(eid: int, info: dict) -> tuple[list, list, list, list]:
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

    for r in precinct + county:
        r["source"] = "api"
    filled, recounts, export_issues, zeroed = merge_exports(eid, on, root, races, precinct, county)
    if zeroed:
        precinct = [
            r
            for r in precinct
            if (r["race_id"], _party(r["party_ballot"]), r["county"]) not in zeroed
        ]
    precinct += filled
    issues += export_issues

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
                r.get("precinct_seq"),
                r["candidate_id"] or r["candidate"],
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
    return precinct, county, recounts, issues


def main() -> None:
    parser = argparse.ArgumentParser(description="Build precinct results from captured raw files")
    parser.add_argument("--eid", type=int, action="append")
    args = parser.parse_args()
    catalog = {int(r["eid"]): r for r in csv.DictReader(CATALOG.open())}
    eids = args.eid or sorted(int(p.name) for p in RAW.iterdir() if (p / "races.json").exists())

    precinct, county, recount, issues = [], [], [], []
    for eid in eids:
        p, c, r, i = build_one(eid, catalog[eid])
        precinct += p
        county += c
        recount += r
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
    pd.DataFrame(recount).to_parquet(OUT / "recount_results.parquet", index=False)
    (OUT / "checks.json").write_text(json.dumps(issues, indent=1, ensure_ascii=False))
    n_recounts = len({(r["eid"], r["race_id"], r["county"]) for r in recount})
    print(
        f"\n{len(precinct):,} precinct rows, {len(county):,} county rows, "
        f"{len(recount):,} recount rows ({n_recounts} races × counties) → {OUT}"
    )
    for i in issues:
        print(f"  [{i['eid']}] {i['check']}: {i['detail'][:220]}")


if __name__ == "__main__":
    main()
