"""Capture every race's precinct results for elections on electionresults.sd.gov.

    uv run python pipelines/elections/capture.py --eid 684          # one election
    uv run python pipelines/elections/capture.py --all              # every election in the catalog
    uv run python pipelines/elections/capture.py --all --refresh-live   # recapture unofficial ones

How it works, for each election (eid):

1. Pages. Starting at default.aspx?eid=N, follow every results page and list on the site:
   statewide races, ballot questions, legislative races, county and local race lists, and the
   per-county and per-place pages they link to. Older elections (2008) have no per-county
   pages, so following links, rather than assuming a layout, is what finds every race.
2. Races. Each page's "Precinct Results" and "County Results" buttons call
   BuildPrecincts(type, raceID, osn, countyID, party) or BuildCounty(type, raceID, osn, party).
   Every distinct (type, raceID, osn, party) is one race on the ballot.
3. Results. For each race, the site's own API (GetMapDataArchive):
   - category=PREC, county=0: every precinct in the state that voted on the race, in one call.
     If that comes back empty, each county the race appeared in is asked for separately.
   - category=CTY, county=0, for races with county buttons: county totals, for checking that
     the precinct results add up.

Everything fetched is saved raw under .cache/elections/raw/<eid>/ (pages/, api/) with a
manifest.jsonl line per request, and is never fetched twice (unless --refresh-live for an
unofficial election). races.json lists the races found. build.py turns the raw JSON into one
table. One request at a time, with a pause; interrupting and rerunning picks up where it left.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit

import requests

SITE = "https://electionresults.sd.gov/"
API = "https://sdresws.azurewebsites.us/ResultsAjax.svc/GetMapDataArchive"
HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
RAW = REPO / ".cache" / "elections" / "raw"
CATALOG = HERE / "data" / "elections.csv"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36"
)
ERROR_MARK = "An error has occured"  # sic

# Results pages worth following; turnout, terminology, media and export pages aren't races
FOLLOW = re.compile(r"^(results(sw|list)\.aspx)$", re.I)
CALL = re.compile(r"\b(BuildPrecincts|BuildCounty)\(([^)]*)\)")
TAGS = re.compile(r"<[^>]+>")


def turnout(page: str) -> dict:
    """The home page's statewide counts: {"ballots_cast": 0, "registered_voters": 1, …}."""
    text = " ".join(
        html.unescape(TAGS.sub(" ", re.sub(r"(?s)<(script|style).*?</\1>", "", page))).split()
    )
    out = {}
    for label in ("Ballots Cast", "Registered Voters"):
        if m := re.search(rf"{label}\s+([\d,]+)", text):
            out[label.lower().replace(" ", "_")] = int(m[1].replace(",", ""))
    return out


class Fetcher:
    def __init__(self, eid: int, delay: float, refresh: bool):
        self.eid, self.delay, self.refresh = eid, delay, refresh
        self.root = RAW / str(eid)
        self.session = requests.Session()
        self.session.headers["User-Agent"] = UA
        self.requests = 0

    def get(self, url: str, path: Path) -> str:
        if path.exists() and not self.refresh:
            return path.read_text()
        for attempt in range(4):
            try:
                if self.requests:
                    time.sleep(self.delay)
                self.requests += 1
                resp = self.session.get(url, timeout=120)
                resp.raise_for_status()
                break
            except requests.RequestException:
                if attempt == 3:
                    raise
                time.sleep(10 * (attempt + 1))
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".partial")
        tmp.write_text(resp.text)
        tmp.replace(path)  # saved whole or not at all
        with (self.root / "manifest.jsonl").open("a") as f:
            f.write(
                json.dumps(
                    {
                        "url": url,
                        "path": str(path.relative_to(self.root)),
                        "bytes": len(resp.content),
                        "sha256": hashlib.sha256(resp.content).hexdigest(),
                        "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
                    }
                )
                + "\n"
            )
        return resp.text


def page_name(url: str) -> str:
    """A stable file name for a results page URL: resultsSW_type=CTY_cty=01_eid=684.html."""
    parts = urlsplit(url)
    query = "_".join(
        f"{k}={v}" for k, v in sorted(parse_qsl(parts.query)) if k not in ("map", "name")
    )
    stem = Path(parts.path).stem
    return re.sub(r"[^A-Za-z0-9=._-]", "-", f"{stem}_{query}")[:180] + ".html"


def normalize(url: str, eid: int) -> str:
    """Absolute URL with this election's eid, without the share-button noise."""
    parts = urlsplit(urljoin(SITE, html.unescape(url)))
    query = dict(parse_qsl(parts.query))
    query["eid"] = str(eid)
    return f"{SITE}{parts.path.lstrip('/')}?{urlencode(sorted(query.items()))}"


def links(page: str, eid: int) -> list[str]:
    out = []
    for href in re.findall(r'href="([^"]+)"', page):
        href = html.unescape(href)
        if href.startswith(("http://www.facebook", "https://twitter", "mailto:")):
            continue
        path = urlsplit(urljoin(SITE, href)).path.rsplit("/", 1)[-1]
        if FOLLOW.match(path) and "RECOUNT" not in href.upper():
            out.append(normalize(href, eid))
    return out


def calls(page: str) -> list[dict]:
    """The race buttons on a page."""
    out = []
    for fn, args in CALL.findall(page):
        a = [x.strip().strip("'\"").strip() for x in args.split(",")]
        if fn == "BuildPrecincts":
            a += [""] * (5 - len(a))
            out.append(
                {"fn": fn, "type": a[0], "race": a[1], "osn": a[2], "county": a[3], "party": a[4]}
            )
        else:
            a += [""] * (4 - len(a))
            out.append(
                {"fn": fn, "type": a[0], "race": a[1], "osn": a[2], "county": "", "party": a[3]}
            )
    return out


def rows_of(text: str) -> list[dict]:
    """API answers come as a bare list or wrapped in {"d": [...]} (sometimes as a JSON string)."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        data = data.get("d", [])
    if isinstance(data, str):
        data = json.loads(data)
    return data if isinstance(data, list) else []


def api_url(race: dict, category: str, county: str) -> str:
    party = race["party"] or "0"
    q = {
        "type": race["type"],
        "category": category,
        "raceID": race["race"],
        "osn": race["osn"],
        "county": county or "0",
        "party": party,
        "electionID": race["eid"],
    }
    return f"{API}?{urlencode(q)}"


def api_path(root: Path, race: dict, category: str, county: str) -> Path:
    party = race["party"] or "0"
    name = f"{race['type']}-{race['race']}-{race['osn']}-p{party}-{category}-c{county or '0'}.json"
    return root / "api" / re.sub(r"[^A-Za-z0-9._-]", "-", name)


def capture(eid: int, delay: float, refresh: bool, log) -> dict:
    f = Fetcher(eid, delay, refresh)
    f.root.mkdir(parents=True, exist_ok=True)

    # 1. Pages: breadth-first over results pages, two levels below the home page
    start = f"{SITE}default.aspx?eid={eid}"
    seen, queue, pages = {start}, [(start, 0)], {}
    while queue:
        url, depth = queue.pop(0)
        text = f.get(url, f.root / "pages" / page_name(url))
        if ERROR_MARK in text:
            continue
        pages[url] = text
        if depth < 3:
            for link in links(text, eid):
                if link not in seen:
                    seen.add(link)
                    queue.append((link, depth + 1))

    # 2. Races
    races: dict[tuple, dict] = {}
    for url, text in pages.items():
        for c in calls(text):
            k = (c["type"], c["race"], c["osn"], c["party"])
            r = races.setdefault(
                k,
                {
                    **{f: c[f] for f in ("type", "race", "osn", "party")},
                    "eid": eid,
                    "counties": set(),
                    "county_totals": False,
                    "pages": set(),
                },
            )
            if c["county"]:
                r["counties"].add(c["county"])
            if c["fn"] == "BuildCounty":
                r["county_totals"] = True
            r["pages"].add(page_name(url))
    log(f"  eid {eid}: {len(pages)} pages, {len(races)} races")

    # 3. Results
    precinct_rows = 0
    for r in races.values():
        text = f.get(api_url(r, "PREC", "0"), api_path(f.root, r, "PREC", "0"))
        got = rows_of(text)
        r["precinct_source"] = "statewide"
        if not got and r["counties"]:
            r["precinct_source"] = "by county"
            for county in sorted(r["counties"]):
                got += rows_of(
                    f.get(api_url(r, "PREC", county), api_path(f.root, r, "PREC", county))
                )
        r["precinct_rows"] = len(got)
        precinct_rows += len(got)
        if r["county_totals"]:
            r["county_rows"] = len(
                rows_of(f.get(api_url(r, "CTY", "0"), api_path(f.root, r, "CTY", "0")))
            )

    home = turnout(pages.get(start, ""))
    summary = [
        {**r, "counties": sorted(r["counties"]), "pages": sorted(r["pages"])}
        for r in sorted(races.values(), key=lambda r: (r["type"], int(r["osn"] or 0), r["race"]))
    ]
    (f.root / "races.json").write_text(json.dumps(summary, indent=1))
    empty = [r for r in summary if not r["precinct_rows"]]
    result = {
        "eid": eid,
        "pages": len(pages),
        "races": len(races),
        "precinct_rows": precinct_rows,
        "races_without_rows": len(empty),
        **home,
    }
    (f.root / "summary.json").write_text(json.dumps(result, indent=1))
    note = " (the site has no results: 0 ballots cast)" if home.get("ballots_cast") == 0 else ""
    log(
        f"  eid {eid}: {precinct_rows:,} precinct rows; {len(empty)} races with none; "
        f"{f.requests} requests{note}"
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Capture precinct results from electionresults.sd.gov"
    )
    parser.add_argument(
        "--eid", type=int, action="append", help="an election to capture (repeatable)"
    )
    parser.add_argument("--all", action="store_true", help="every election in data/elections.csv")
    parser.add_argument("--delay", type=float, default=1.0, help="seconds between requests")
    parser.add_argument("--refresh-live", action="store_true", help="refetch unofficial elections")
    args = parser.parse_args()

    catalog = {int(r["eid"]): r for r in csv.DictReader(CATALOG.open())}
    eids = sorted(catalog) if args.all else (args.eid or [])
    if not eids:
        parser.error("give --eid N or --all")

    def log(msg: str) -> None:
        print(msg, flush=True)

    for eid in eids:
        info = catalog.get(eid, {})
        log(
            f"eid {eid}: {info.get('date', '?')} {info.get('title', '')} "
            f"({info.get('status', '?')})"
        )
        refresh = args.refresh_live and info.get("status") == "unofficial"
        capture(eid, args.delay, refresh, log)


if __name__ == "__main__":
    main()
