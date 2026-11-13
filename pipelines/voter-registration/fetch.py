"""Find the Secretary of State's voter registration reports, and add new ones as snapshots.

    uv run python pipelines/voter-registration/fetch.py

The "Registered Voters by County" page lists a PDF for each report: monthly since December
2015, plus the totals at each election. Each link's text gives the report's date ("October
2, 2026"); legal citations nearby ("SDCL 12-5-1.5") aren't read as dates.

A snapshot is dated by the date printed in its report, which isn't always the link's: the
"July 28, 2026" link is the July 31 report, and a notice about a party's status links to the
November 20, 2018 report. sources.json records each link: its URL, the link's date and the
report's.

For each link not yet in sources.json, the PDF is downloaded (to
.cache/voter-registration/raw/pdfs/<report date>.pdf) and read and checked (parse.py). If its
report isn't in snapshots/ yet, any correction in fixes.toml is applied and the snapshot is
written to snapshots/<date>.csv. A report that fails a check is reported and left out, and
nothing is written for it, so it's tried again on the next run.
"""

from __future__ import annotations

import csv
import json
import sys
import tomllib
from pathlib import Path
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit

import parse  # this folder's parse.py (the script runs from here, so it's on the path)

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
RAW = REPO / ".cache" / "voter-registration" / "raw"
SNAPSHOTS = HERE / "snapshots"
SOURCES = HERE / "sources.json"
PAGE = ("https://sdsos.gov/elections-voting/upcoming-elections/voter-registration-totals/"
        "voter-registration-by-county.aspx")  # fmt: skip


def _url(href: str) -> str:
    """An absolute URL with the spaces some file names have encoded."""
    parts = urlsplit(urljoin(PAGE, href))
    return urlunsplit(parts._replace(path=quote(unquote(parts.path))))


def reports(html: str) -> list[dict]:
    """The PDF links in the page's content section: URL and the date in the link's text (or
    None)."""
    import lxml.html

    root = lxml.html.fromstring(html)
    content = root.xpath("//section[contains(concat(' ', @class, ' '), ' centerer ')]")
    out, seen = [], set()
    for a in (content[0] if content else root).iter("a"):
        href = a.get("href") or ""
        if not href.lower().endswith(".pdf"):
            continue
        url = _url(href)
        if url in seen:
            continue
        seen.add(url)
        text = " ".join(a.text_content().split())
        out.append({"url": url, "text": text, "link_date": parse.report_date(text)})
    return out


def apply_fixes(date: str, rows: list[dict], fixes: dict) -> list[dict]:
    """A report's corrections from fixes.toml: swapped columns."""
    fix = fixes.get(date)
    if not fix:
        return rows
    a, b = fix["swap"]
    return [{**r, a: r[b], b: r[a]} for r in rows]


def write_snapshot(date: str, parties: list[str], rows: list[dict]) -> Path:
    path = SNAPSHOTS / f"{date}.csv"
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, ["date", "county", *parties], extrasaction="ignore")
        w.writeheader()
        w.writerows({"date": date, **r} for r in rows)
    return path


def update(fetcher, links: list[dict] | None = None) -> tuple[list[str], list[str]]:
    """Read the links not yet in sources.json, and add their reports not yet in snapshots/:
    (dates added, problems)."""
    links = links or reports(fetcher.request("GET", PAGE, timeout=60).text)
    if not links:
        raise ValueError("no reports on the page")
    sources = json.loads(SOURCES.read_text()) if SOURCES.exists() else {}
    fixes = tomllib.loads((HERE / "fixes.toml").read_text())
    have = {p.stem for p in SNAPSHOTS.glob("*.csv")}
    added, problems = [], []
    for link in links:
        if link["url"] in sources:
            continue
        content = fetcher.request("GET", link["url"], timeout=120).content
        try:
            date, parties, rows = parse.read(content)
        except parse.ReportProblem as e:
            fetcher.store.save(f"pdfs/unread/{link['link_date'] or 'undated'}.pdf", content,
                               link["url"])  # fmt: skip
            problems.append(f"{link['url']} ({link['text']}): {e}")
            continue
        fetcher.store.save(f"pdfs/{date}.pdf", content, link["url"])
        sources[link["url"]] = {"report_date": date, "link_date": link["link_date"],
                                "link_text": link["text"]}  # fmt: skip
        if date not in have:
            write_snapshot(date, parties, apply_fixes(date, rows, fixes))
            have.add(date)
            added.append(date)
    SOURCES.write_text(json.dumps(dict(sorted(sources.items(),
                                              key=lambda kv: kv[1]["report_date"])),
                                  indent=1) + "\n")  # fmt: skip
    return added, problems


def main() -> int:
    from sdfetch import Fetcher

    with Fetcher(RAW) as fetcher:
        added, problems = update(fetcher)
    print(f"added: {', '.join(added) or 'none'}")
    for p in problems:
        print(f"PROBLEM: {p}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
