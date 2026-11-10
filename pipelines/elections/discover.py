"""Catalog every election on electionresults.sd.gov.

The site serves one election at a time, chosen by ?eid=. IDs are sparse (eid=1 is the
2008 general; most numbers in between are empty), and an unknown eid returns a short
error page. This checks every eid from 1 up, saves each page, and writes a catalog.

    uv run python pipelines/elections/discover.py            # resumes where it stopped
    uv run python pipelines/elections/discover.py --to 1500

Output
  pipelines/elections/data/elections.csv   the catalog (committed): eid, title, date, status
  .cache/elections/eid/<eid>.html          every raw page (not committed)

It stops after --gap empty eids in a row past the highest one found. Rerunning skips eids
already saved, unless --refresh.
"""

from __future__ import annotations

import argparse
import csv
import html
import re
import time
from datetime import UTC, datetime
from pathlib import Path

import requests

BASE = "https://electionresults.sd.gov/default.aspx?eid={eid}"
HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
RAW = REPO / ".cache" / "elections" / "eid"
CATALOG = HERE / "data" / "elections.csv"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36"
)
ERROR_MARK = "An error has occured"  # sic, the site's spelling
MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
TITLE = re.compile(rf"((?:Un)?[Oo]fficial) Results\s+(.+?)\s+((?:{MONTHS}) \d{{1,2}}, \d{{4}})")
FIELDS = ["eid", "status", "title", "date", "url", "fetched_at"]


def text_of(page: str) -> str:
    page = re.sub(r"<script.*?</script>|<style.*?</style>", " ", page, flags=re.S)
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", page)).split())


def parse(eid: int, page: str) -> dict | None:
    """The catalog row for one page, or None for the error page (no election)."""
    if ERROR_MARK in page:
        return None
    m = TITLE.search(text_of(page))
    if not m:
        return {
            "eid": eid,
            "status": "",
            "title": "UNPARSED",
            "date": "",
            "url": BASE.format(eid=eid),
        }
    status, title, when = m.groups()
    return {
        "eid": eid,
        "status": status.lower(),
        "title": title.strip(),
        "date": datetime.strptime(when, "%B %d, %Y").date().isoformat(),
        "url": BASE.format(eid=eid),
    }


def fetch(session: requests.Session, eid: int, refresh: bool) -> str:
    path = RAW / f"{eid}.html"
    if path.exists() and not refresh:
        return path.read_text()
    for attempt in range(4):
        try:
            resp = session.get(BASE.format(eid=eid), timeout=30)
            resp.raise_for_status()
            break
        except requests.RequestException:
            if attempt == 3:
                raise
            time.sleep(5 * (attempt + 1))
    tmp = path.with_suffix(".partial")
    tmp.write_text(resp.text)
    tmp.replace(path)  # a page is either fully saved or not at all
    return resp.text


def main() -> None:
    parser = argparse.ArgumentParser(description="Catalog every election on electionresults.sd.gov")
    parser.add_argument("--from", dest="start", type=int, default=1)
    parser.add_argument(
        "--to", type=int, default=0, help="last eid to check (default: until --gap)"
    )
    parser.add_argument("--gap", type=int, default=400, help="empty eids in a row to stop after")
    parser.add_argument("--delay", type=float, default=1.0, help="seconds between requests")
    parser.add_argument("--refresh", action="store_true", help="refetch pages already saved")
    args = parser.parse_args()

    RAW.mkdir(parents=True, exist_ok=True)
    CATALOG.parent.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers["User-Agent"] = UA

    found: dict[int, dict] = {}
    eid, last_hit = args.start, args.start - 1
    while True:
        if args.to and eid > args.to:
            break
        if not args.to and eid - last_hit > args.gap:
            break
        cached = (RAW / f"{eid}.html").exists() and not args.refresh
        page = fetch(session, eid, args.refresh)
        row = parse(eid, page)
        if row:
            row["fetched_at"] = datetime.fromtimestamp(
                (RAW / f"{eid}.html").stat().st_mtime, UTC
            ).isoformat(timespec="seconds")
            found[eid] = row
            last_hit = eid
            print(f"{eid:>5} {row['date']} {row['status']:<10} {row['title']}", flush=True)
        if not cached:
            time.sleep(args.delay)
        eid += 1

    with CATALOG.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(found[k] for k in sorted(found))
    print(f"checked eids {args.start}–{eid - 1}: {len(found)} elections → {CATALOG}")


if __name__ == "__main__":
    main()
