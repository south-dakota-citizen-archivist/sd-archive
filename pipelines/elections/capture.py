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
import html
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit

import sdfetch

SITE = "https://electionresults.sd.gov/"
API = "https://sdresws.azurewebsites.us/ResultsAjax.svc/GetMapDataArchive"
HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
RAW = REPO / ".cache" / "elections" / "raw"
CATALOG = HERE / "data" / "elections.csv"
API_LIVE = "https://sdresws.azurewebsites.us/ResultsAjax.svc/GetMapData"
ERROR_MARK = "An error has occured"  # sic
HIDDEN = re.compile(r'<input type="hidden" name="([^"]+)" id="[^"]*" value="([^"]*)"')
POSTBACK = re.compile(r"__doPostBack\(&#39;([^&]+)&#39;,&#39;&#39;\)\">([^<]+)<")

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
    """sdfetch's Fetcher with this election's folder (raw/<eid>) as its store. Paths given to
    get() and fetch() are inside that folder; refresh fetches them again."""

    def __init__(self, eid: int, delay: float, refresh: bool):
        self.eid, self.refresh = eid, refresh
        self.root = RAW / str(eid)
        self.http = sdfetch.Fetcher(self.root, delay=delay, timeout=300)

    @property
    def requests(self) -> int:
        return self.http.requests

    def get(self, url: str, path: Path) -> str:
        return self.fetch(url, path).decode("utf-8", "replace")

    def fetch(
        self, url: str, path: Path, form: dict | None = None, note: dict | None = None
    ) -> bytes:
        """The response body, saved at path. A POST when form is given (the export postbacks)."""
        name = path.relative_to(self.root).as_posix()
        return self.http.fetch(url, name, data=form, note=note, refresh=self.refresh)


def page_name(url: str) -> str:
    """A stable file name for a results page URL: resultsSW_type=CTY_cty=01_eid=684.html."""
    parts = urlsplit(url)
    query = "_".join(
        f"{k}={v}" for k, v in sorted(parse_qsl(parts.query)) if k not in ("map", "name")
    )
    stem = Path(parts.path).stem
    return re.sub(r"[^A-Za-z0-9=._-]", "-", f"{stem}_{query}")[:180] + ".html"


def normalize(url: str, eid: int, live: bool = False) -> str:
    """Absolute URL with this election's eid (none for the live election), without noise."""
    parts = urlsplit(urljoin(SITE, html.unescape(url)))
    query = dict(parse_qsl(parts.query))
    query.pop("eid", None)
    if not live:
        query["eid"] = str(eid)
    return f"{SITE}{parts.path.lstrip('/')}?{urlencode(sorted(query.items()))}"


def links(page: str, eid: int, live: bool = False) -> list[str]:
    out = []
    for href in re.findall(r'href="([^"]+)"', page):
        href = html.unescape(href)
        if href.startswith(("http://www.facebook", "https://twitter", "mailto:")):
            continue
        path = urlsplit(urljoin(SITE, href)).path.rsplit("/", 1)[-1]
        if FOLLOW.match(path) and "RECOUNT" not in href.upper():
            out.append(normalize(href, eid, live))
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


def api_url(race: dict, category: str, county: str, live: bool = False) -> str:
    """The archive API, or for the live election the current one (no electionID)."""
    party = race["party"] or "0"
    q = {
        "type": race["type"],
        "category": category,
        "raceID": race["race"],
        "osn": race["osn"],
        "county": county or "0",
        "party": party,
    }
    if live:
        return f"{API_LIVE}?{urlencode(q)}"
    return f"{API}?{urlencode(q | {'electionID': race['eid']})}"


def election_date(page: str) -> str:
    """The hidden election date on any results page, as ISO: '11/3/2026' → '2026-11-03'."""
    m = re.search(r'hidElectionDate" value="(\d+)/(\d+)/(\d{4})', page)
    return f"{m[3]}-{int(m[1]):02d}-{int(m[2]):02d}" if m else ""


def exports(f: Fetcher, eid: int, live: bool, log) -> dict:
    """Every spreadsheet on the Exports page: statewide, legislative and each county.

    They're ASP.NET postbacks, so the page's form is read once and each link's event target
    posted back. Each export has a sheet per race with a row per precinct, and fills holes in
    the API (2012's precincts; a few races it has no rows for).
    """
    url = normalize("ResultsExport.aspx", eid, live)
    page_path = f.root / "exports" / "ResultsExport.html"

    def read_page() -> tuple[dict, list]:
        text = page_path.read_text(errors="replace")
        form = {k: html.unescape(v) for k, v in HIDDEN.findall(text)}
        return form, [
            (html.unescape(t), html.unescape(n).strip()) for t, n in POSTBACK.findall(text)
        ]

    def out(name: str) -> Path:
        return f.root / "exports" / (re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-") + ".xlsx")

    if not page_path.exists():
        f.fetch(url, page_path)
    form, targets = read_page()

    def done(name: str) -> bool:  # saved, or the site errors on it (some 2008 counties)
        return out(name).exists() or out(name).with_suffix(".error.html").exists()

    if f.refresh or not all(done(n) for _, n in targets):
        page_path.unlink()  # a fresh form (view state) to post back from
        f.fetch(url, page_path)
        form, targets = read_page()
    saved = failed = 0
    for target, name in targets:
        path = out(name)
        if done(name) and not f.refresh:
            saved += path.exists()
            failed += not path.exists()
            continue
        body = f.fetch(
            url,
            path,
            form={**form, "__EVENTTARGET": target, "__EVENTARGUMENT": ""},
            note={"event_target": target, "label": name},
        )
        if body[:2] == b"PK":  # an xlsx is a zip
            saved += 1
        else:
            failed += 1
            path.replace(path.with_suffix(".error.html"))
    log(f"  eid {eid}: {saved} exports" + (f", {failed} failed" if failed else ""))
    return {"exports": saved, "exports_failed": failed}


def api_path(root: Path, race: dict, category: str, county: str) -> Path:
    party = race["party"] or "0"
    name = f"{race['type']}-{race['race']}-{race['osn']}-p{party}-{category}-c{county or '0'}.json"
    return root / "api" / re.sub(r"[^A-Za-z0-9._-]", "-", name)


def is_live(f: Fetcher, eid: int, date: str) -> bool:
    """Whether this election is the site's current one, served without an eid.

    The site's newest election isn't in its archive yet: default.aspx?eid=N shows 0 ballots
    cast, and the results are on default.aspx (no eid), from the current API.
    """
    archived = f.get(f"{SITE}default.aspx?eid={eid}", f.root / "pages" / f"default_eid={eid}.html")
    if turnout(archived).get("ballots_cast") != 0:
        return False
    path = f.root / "pages" / "default_live.html"
    path.unlink(missing_ok=True)  # always ask the site which election is current
    current = f.get(f"{SITE}default.aspx", path)
    return election_date(current) == date and bool(turnout(current).get("ballots_cast"))


def capture(eid: int, delay: float, refresh: bool, log, date: str = "") -> dict:
    f = Fetcher(eid, delay, refresh)
    f.root.mkdir(parents=True, exist_ok=True)
    live = bool(date) and is_live(f, eid, date)
    if live:
        f.refresh = True  # the current election's numbers can still change
        log(f"  eid {eid}: the site's current election; capturing the live results (no eid)")

    # 1. Pages: breadth-first over results pages, two levels below the home page
    start = f"{SITE}default.aspx" if live else f"{SITE}default.aspx?eid={eid}"
    seen, queue, pages = {start}, [(start, 0)], {}
    while queue:
        url, depth = queue.pop(0)
        name = page_name(url).replace(".html", "_live.html") if live else page_name(url)
        text = f.get(url, f.root / "pages" / name)
        if ERROR_MARK in text:
            continue
        pages[url] = text
        if depth < 3:
            for link in links(text, eid, live):
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
        text = f.get(api_url(r, "PREC", "0", live), api_path(f.root, r, "PREC", "0"))
        got = rows_of(text)
        r["precinct_source"] = "statewide"
        if not got and r["counties"]:
            r["precinct_source"] = "by county"
            for county in sorted(r["counties"]):
                got += rows_of(
                    f.get(api_url(r, "PREC", county, live), api_path(f.root, r, "PREC", county))
                )
        r["precinct_rows"] = len(got)
        precinct_rows += len(got)
        if r["county_totals"]:
            r["county_rows"] = len(
                rows_of(f.get(api_url(r, "CTY", "0", live), api_path(f.root, r, "CTY", "0")))
            )

    # 4. Exports
    exported = exports(f, eid, live, log)

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
        **exported,
        **home,
        "live": live,
        "captured_at": datetime.now(UTC).isoformat(timespec="seconds"),
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
        capture(eid, args.delay, refresh, log, info.get("date", ""))


if __name__ == "__main__":
    main()
