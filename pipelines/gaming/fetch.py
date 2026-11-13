"""Download the Commission on Gaming's monthly statistics from the Department of Revenue's
gaming page.

    uv run python pipelines/gaming/fetch.py

Saves files.json (each report in the page's "South Dakota Gaming Statistics" section: its
heading, link text and URL) and each report under .cache/gaming/raw/: monthly/<Month>-<Year>.pdf
for the monthly summaries (2023 on), annual/<link text>.pdf for the earlier years' files. A
report already saved isn't fetched again, except this year's and last year's (in case the
Commission replaces a file).
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from urllib.parse import urljoin

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
RAW = REPO / ".cache" / "gaming" / "raw"
PAGE = "https://dor.sd.gov/businesses/gaming/"
MONTH = re.compile(r"^(January|February|March|April|May|June|July|August|September|October|"
                   r"November|December) (\d{4})$")  # fmt: skip


def reports(html: str) -> list[dict]:
    """The PDFs under the page's "South Dakota Gaming Statistics" heading, with the heading
    above each (Gaming 2026, …, Gaming Statistics Previous Calendar Years)."""
    import lxml.html

    root = lxml.html.fromstring(html)
    main = (root.xpath("//main") or [root])[0]
    out, section, heading = [], None, None
    for el in main.iter():
        if not isinstance(el.tag, str):
            continue  # comments
        if el.tag in ("h1", "h2", "h3"):
            section, heading = " ".join(el.text_content().split()), None
        elif el.tag in ("h4", "h5", "strong", "button"):
            text = " ".join(el.text_content().split())
            if 0 < len(text) < 80:
                heading = text
        elif (el.tag == "a" and section and "Gaming Statistics" in section
              and (el.get("href") or "").lower().endswith(".pdf")):  # fmt: skip
            text = " ".join(el.text_content().split())
            monthly = MONTH.match(text)
            out.append({"section": heading, "text": text, "url": urljoin(PAGE, el.get("href")),
                        "file": (f"monthly/{text.replace(' ', '-')}.pdf" if monthly
                                 else f"annual/{text.replace(' ', '-')}.pdf")})  # fmt: skip
    return out


def fetch(fetcher, today: date | None = None) -> list[dict]:
    today = today or date.today()
    files = reports(fetcher.request("GET", PAGE, timeout=60).text)
    if not files:
        raise ValueError("no gaming statistics on the page")
    for f in files:
        recent = str(today.year) in f["text"] or str(today.year - 1) in f["text"]
        if recent or not fetcher.store.has(f["file"]):
            fetcher.store.save(f["file"], fetcher.request("GET", f["url"], timeout=120).content,
                               f["url"])  # fmt: skip
    (RAW / "files.json").write_text(json.dumps(files, indent=1))
    return files


def main() -> None:
    from sdfetch import Fetcher

    with Fetcher(RAW) as fetcher:
        files = fetch(fetcher)
    monthly = [f for f in files if f["file"].startswith("monthly/")]
    print(f"{len(monthly)} monthly reports, {len(files) - len(monthly)} other files")


if __name__ == "__main__":
    main()
