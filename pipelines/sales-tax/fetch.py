"""Download the Department of Revenue's monthly sales tax statistical reports, as listed on
its historical sales tax reports page.

    uv run python pipelines/sales-tax/fetch.py

Saves reports.json (every report on the page: the year section, the report, the link's text
and URL, and the month the file is for) and the municipal tax by city and SIC code files
(Excel) as .cache/sales-tax/raw/municipal-by-sic/<yyyy-mm>.xlsx. A month already saved isn't
fetched again, except the two latest (in case the Department revises them).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import unquote, urljoin

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
RAW = REPO / ".cache" / "sales-tax" / "raw"
PAGE = "https://dor.sd.gov/businesses/taxes/sales-use-tax/historical-tax-statistical-reports/"
MUNICIPAL_BY_SIC = "Municipal Taxable Sales per City by SIC Code"
# a file's month from its name: "0126-sd-municipal-by-sic.xlsx", "10-22-sd-municipal-by-sic.xlsx",
# "0125municipalmonthlytaxbysic.pdf"
MONTH_IN_NAME = re.compile(r"^(?P<m>0[1-9]|1[0-2])-?(?P<y>\d{2})(?=[-a-z])", re.I)


def month_of(url: str) -> str | None:
    """yyyy-mm from a monthly report's file name; None for an annual or unusual one."""
    m = MONTH_IN_NAME.match(unquote(url.rsplit("/", 1)[-1]))
    return f"20{m['y']}-{m['m']}" if m else None


def listing(html: str) -> list[dict]:
    """Every report file on the page, with its year section and report heading."""
    import lxml.html

    root = lxml.html.fromstring(html)
    main = (root.xpath("//main") or [root])[0]
    out, section, report = [], None, None
    for el in main.iter():
        text = " ".join((el.text_content() or "").split()) if el.tag != "a" else ""
        if el.tag in ("h1", "h2", "h3"):
            section, report = text, None
        elif el.tag in ("h4", "h5", "strong", "button") and 0 < len(text) < 80:
            report = text
        elif el.tag == "a" and re.search(r"\.(pdf|xlsx?)(\?|$)", el.get("href") or "", re.I):
            url = urljoin(PAGE, el.get("href"))
            out.append({"section": section, "report": report,
                        "text": " ".join(el.text_content().split()), "url": url,
                        "month": month_of(url),
                        "format": url.rsplit(".", 1)[-1].lower()})  # fmt: skip
    return out


def municipal_by_sic(files: list[dict]) -> dict[str, str]:
    """yyyy-mm → URL of each month's municipal tax by city and SIC code workbook."""
    out = {}
    for f in files:
        if f["report"] == MUNICIPAL_BY_SIC and f["format"] == "xlsx" and f["month"]:
            if out.get(f["month"], f["url"]) != f["url"]:
                raise ValueError(f"two workbooks for {f['month']}: {out[f['month']]}, {f['url']}")
            out[f["month"]] = f["url"]
    return dict(sorted(out.items()))


def fetch(fetcher) -> dict[str, str]:
    files = listing(fetcher.request("GET", PAGE, timeout=60).text)
    months = municipal_by_sic(files)
    if not months:
        raise ValueError("no municipal tax workbooks on the page")
    latest = sorted(months)[-2:]
    for month, url in months.items():
        name = f"municipal-by-sic/{month}.xlsx"
        if month in latest or not fetcher.store.has(name):
            fetcher.store.save(name, fetcher.request("GET", url, timeout=300).content, url)
    (RAW / "reports.json").write_text(json.dumps(files, indent=1))
    (RAW / "municipal-by-sic.json").write_text(json.dumps(months, indent=1))
    return months


def main() -> None:
    from sdfetch import Fetcher

    with Fetcher(RAW) as fetcher:
        months = fetch(fetcher)
    print(f"municipal tax by SIC: {len(months)} months ({min(months)} to {max(months)})")


if __name__ == "__main__":
    main()
