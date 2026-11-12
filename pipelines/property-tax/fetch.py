"""Download the Department of Revenue's property tax levy files from its data page
(sdproptax.info/DataLink/Data): the county, city/town, township, road district, other
district and school district recaps (Excel), and the PT-74 files (CSV). The page's sales
files are a record set of their own (property-sales), not read here. Also the annual
Property Tax Statistical Reports (PDF), listed on the Department's statistics page.

    uv run python pipelines/property-tax/fetch.py

Saves each file in .cache/property-tax/raw/files/, and files.json: each file's URL, the
section of the page it's listed under, and the date the page says that section was last
updated. Every file is fetched again each run (the Department revises them in place).
The statistical reports go in .cache/property-tax/raw/statistical-reports/<year>.pdf (with
statistical-reports.json); a report already saved isn't fetched again.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import unquote, urljoin

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
RAW = REPO / ".cache" / "property-tax" / "raw"
PAGE = "https://sdproptax.info/DataLink/Data"
STATISTICS = "https://sddor.seamlessdocs.com/sc/api/folders/v2/statistics/active-details"

# file name → (kind, year)
KINDS = {
    "County Recap": "county", "City_Town Recap": "city_town", "Township Recap": "township",
    "Road Dist Recap": "road", "Other Recap": "other", "School Recap": "school",
}  # fmt: skip
FILE = re.compile(r"^(?:(?P<kind>.+?)-(?P<year>\d{4})\.xlsx|PT74_(?P<pt74>\d{4})\.csv)$")
TOKEN = re.compile(
    r'<h5[^>]*>(?P<section>.*?)</h5>|Last Updated:\s*(?P<updated>[\d/]+)'
    r'|href="(?P<href>[^"]*Download\?filename=[^"]+)"',
    re.S,
)  # fmt: skip


def kind_of(name: str) -> tuple[str, int] | None:
    """A file's kind ("county", …, "pt74") and year, or None for files read elsewhere."""
    m = FILE.match(name)
    if not m:
        return None
    if m["pt74"]:
        return "pt74", int(m["pt74"])
    kind = KINDS.get(m["kind"])
    return (kind, int(m["year"])) if kind else None


def listing(html: str) -> dict[str, dict]:
    """File name → its URL, kind, year, section and the section's last-updated date."""
    out, section, updated = {}, None, None
    for m in TOKEN.finditer(html):
        if m["section"] is not None:
            section, updated = " ".join(re.sub(r"<[^>]+>", " ", m["section"]).split()), None
        elif m["updated"]:
            updated = m["updated"]
        else:
            href = m["href"].replace("&amp;", "&")
            name = unquote(href.split("filename=", 1)[1])
            found = kind_of(name)
            if found:
                out[name] = {"url": urljoin(PAGE, href), "kind": found[0], "year": found[1],
                             "section": section, "last_updated": updated}  # fmt: skip
    return out


def statistical_reports(listing: dict) -> dict[int, str]:
    """Year → URL of each Property Tax Statistical Report on the statistics page: the links
    in the lists under its heading, up to the next heading."""
    import lxml.html

    root = lxml.html.fromstring(listing["welcomeLetter"]["content"])
    heading = next((h for h in root.iter("h1", "h2", "h3", "h4")
                    if " ".join(h.text_content().split()).lower()
                    == "property tax statistical reports"), None)  # fmt: skip
    if heading is None:
        raise ValueError("no Property Tax Statistical Reports list on the statistics page")
    out = {}
    el = heading.getnext()
    while el is not None and el.tag not in ("h1", "h2", "h3", "h4"):
        for a in el.iter("a"):
            if (m := re.fullmatch(r"\s*(\d{4})\s*", a.text_content())) and a.get("href"):
                out[int(m[1])] = a.get("href")
        el = el.getnext()
    return out


def fetch_reports(fetcher) -> dict[int, str]:
    reports = statistical_reports(fetcher.request("GET", STATISTICS, timeout=60).json())
    if not reports:
        raise ValueError("no statistical reports on the statistics page")
    for year, url in reports.items():
        name = f"statistical-reports/{year}.pdf"
        if not fetcher.store.has(name):
            fetcher.store.save(name, fetcher.request("GET", url, timeout=300).content, url)
    (RAW / "statistical-reports.json").write_text(json.dumps(reports, indent=1))
    return reports


def fetch(fetcher) -> dict[str, dict]:
    files = listing(fetcher.request("GET", PAGE, timeout=60).text)
    if not files:
        raise ValueError("no levy files on the data page")
    for name, f in files.items():
        content = fetcher.request("GET", f["url"], headers={"Referer": PAGE}, timeout=300).content
        fetcher.store.save(f"files/{name}", content, f["url"])
    (RAW / "files.json").write_text(json.dumps(files, indent=1))
    return files


def main() -> None:
    from sdfetch import Fetcher

    with Fetcher(RAW) as fetcher:
        files = fetch(fetcher)
        reports = fetch_reports(fetcher)
    for name in sorted(files):
        print(f"{name}: {(RAW / 'files' / name).stat().st_size:,} bytes")
    for year in sorted(reports):
        print(
            f"statistical report {year}: "
            f"{(RAW / 'statistical-reports' / f'{year}.pdf').stat().st_size:,} bytes"
        )


if __name__ == "__main__":
    main()
