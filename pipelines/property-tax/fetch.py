"""Download the Department of Revenue's property tax levy files from its data page
(sdproptax.info/DataLink/Data): the county, city/town, township, road district, other
district and school district recaps (Excel), and the PT-74 files (CSV). The page's sales
files are a record set of their own (property-sales), not read here.

    uv run python pipelines/property-tax/fetch.py

Saves each file in .cache/property-tax/raw/files/, and files.json: each file's URL, the
section of the page it's listed under, and the date the page says that section was last
updated. Every file is fetched again each run (the Department revises them in place).
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
    for name in sorted(files):
        print(f"{name}: {(RAW / 'files' / name).stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
