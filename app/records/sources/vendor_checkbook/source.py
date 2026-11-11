"""State payments to vendors (the "vendor checkbook"), from the Bureau of Finance and
Management: three rolling files (the current fiscal year and the two before it), kept here
permanently. One record per payment row as published.

- Keys: a payment has no ID, so each is keyed by payment date, agency, voucher and a hash of
  the row (parse.key). A row the state later corrects is a different key: the old one is
  marked dropped, the new one added.
- A run speaks only for the fiscal years in its files (Found.covers): payments from years
  that have rolled out of the window stay as they are, not dropped.
- Each fetched file is kept (gzip, in the raw store) whenever its content changes.
- Agency names come from the state's agency list, next to each code as published.
- The years before the window (from July 2020) came from an earlier archive
  (import_vendor_checkbook_archive).
"""

from __future__ import annotations

import gzip
from collections import Counter

from records.sources import Found, Item, Source

from . import parse

LEDGER = "https://bfm.sd.gov/ledger/"
FILES = ["CheckbookDetailCurrentYear.csv", "CheckbookDetailPriorYear1.csv",
         "CheckbookDetailPriorYear2.csv"]  # fmt: skip
AGENCIES = "https://bfm.sd.gov/vendor/contactinfo.asp"
STATE = "0400000US46"


def keep(fetcher, name: str, content: bytes, url: str) -> None:
    """The file as fetched, compressed, stored once per distinct content."""
    if getattr(fetcher, "store", None) is not None:
        fetcher.store.blob(gzip.compress(content, mtime=0), url, suffix=".csv.gz",
                           note={"file": name, "bytes_uncompressed": len(content)})  # fmt: skip


def to_items(rows: list[dict], names: dict[str, str], url: str) -> list[Item]:
    seen: Counter = Counter()
    out = []
    for row in rows:
        base = parse.key(row)
        seen[base] += 1
        data = {**row, "agency_name": names.get(row["agency"]),
                "fiscal_year": parse.fiscal_year(row["ap_payment_date"])}  # fmt: skip
        out.append(Item(key=parse.key(row, seen[base]), data=data, source_url=url,
                        places=[STATE]))  # fmt: skip
    return out


def items(fetcher) -> Found:
    names = parse.agencies(fetcher.request("GET", AGENCIES).content)
    out, years = Found(), set()
    for name in FILES:
        url = LEDGER + name
        content = fetcher.request("GET", url, timeout=300).content
        keep(fetcher, name, content, url)
        rows = parse.rows(content)
        years |= {parse.fiscal_year(r["ap_payment_date"]) for r in rows}
        out += to_items(rows, names, url)
    out.checks = {"fiscal_years": sorted(years), "rows": len(out)}
    out.covers = lambda key: parse.fiscal_year(key[:10]) in years
    return out


SOURCE = Source(
    slug="vendor-checkbook", items=items, max_drop=0.2,
    name="State vendor payments", dataset="vendor-checkbook",
    publisher="South Dakota Bureau of Finance and Management", base_url="https://open.sd.gov/vendor.aspx",
    description="Every payment from the state's accounting system to a vendor, as the Bureau "
                "of Finance and Management publishes it for the current and two prior fiscal "
                "years, kept permanently.",
)  # fmt: skip
