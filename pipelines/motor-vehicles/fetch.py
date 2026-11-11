"""Download the Department of Revenue's motor vehicle title and registration statistics:
one PDF per calendar year (the current year's fills in month by month) and a historical
PDF of annual totals. The reports are listed on the Department's SeamlessDocs statistics
page, whose content comes from its folder API.

    uv run python pipelines/motor-vehicles/fetch.py

Saves each report in .cache/motor-vehicles/raw/ (with a manifest). A report for a year
before last is kept once fetched; the historical report and this year's and last year's
are fetched again each run.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
RAW = REPO / ".cache" / "motor-vehicles" / "raw"
LISTING = "https://sddor.seamlessdocs.com/sc/api/folders/v2/statistics/active-details"
PAGE = "https://sddor.seamlessdocs.com/sc/statistics/"


def reports(listing: dict) -> dict[str, str]:
    """Report name ("2025", "historical") → URL, from the Motor Vehicle list on the page."""
    import lxml.html

    root = lxml.html.fromstring(listing["welcomeLetter"]["content"])
    titled = (h for h in root.iter("h1", "h2", "h3")
              if " ".join(h.text_content().split()).lower() == "motor vehicle")  # fmt: skip
    heading = next(titled, None)
    if heading is None:
        raise ValueError("no Motor Vehicle list on the statistics page")
    found = heading.getnext()
    while found is not None and found.tag != "ul":
        found = found.getnext()
    out = {}
    for li in found.iter("li") if found is not None else []:
        a = li.find(".//a[@href]")
        label = " ".join(li.text_content().split()).lower()
        if a is None:
            continue
        if "historical" in label:
            out["historical"] = a.get("href")
        elif m := re.match(r"cy\s*'?(\d{2,4})\b", label):
            year = m.group(1)
            out[year if len(year) == 4 else f"20{year}"] = a.get("href")
    return out


def fetch(fetcher, today: date | None = None) -> dict[str, Path]:
    """Every report, kept in the fetcher's store; name → path."""
    today = today or date.today()
    found = reports(fetcher.request("GET", LISTING, timeout=120).json())
    out = {}
    for name, url in found.items():
        path = f"reports/{name}.pdf"
        fresh = name == "historical" or int(name) >= today.year - 1
        if fresh or not fetcher.store.has(path):
            fetcher.store.save(path, fetcher.request("GET", url, timeout=300).content, url)
        out[name] = fetcher.store.path(path)
    (RAW / "reports.json").write_text(__import__("json").dumps(found, indent=1))
    return out


def main() -> None:
    from sdfetch import Fetcher

    with Fetcher(RAW) as fetcher:
        for name, path in sorted(fetch(fetcher).items()):
            print(f"{name}: {path.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
