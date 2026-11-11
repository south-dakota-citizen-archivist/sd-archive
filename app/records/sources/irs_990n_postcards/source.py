"""Form 990-N e-Postcards (the annual notice small tax-exempt organizations file instead of
a return), from the IRS's bulk e-Postcard file (refreshed monthly; it lists each
organization's latest postcard, so an earlier one stays here, marked dropped): one record per
postcard (organization and tax period) of a South Dakota organization, with its name,
whether its gross receipts were $50,000 or less, whether it has terminated, its website,
its principal officer and address, its mailing address and other names (DBAs).

A postcard is South Dakota's if the organization is in the IRS's South Dakota extract (now,
or in the irs-exempt-organizations record set before) or its mailing address is in South
Dakota. Tied to the mailing address's city; the organization, its other names and its
principal officer are mentioned for matching."""

from __future__ import annotations

import csv
import io
import zipfile

from entities.mentions import from_text
from records.sources import Found, Item, Source
from records.sources.irs_990_returns.source import eins
from records.sources.irs_exempt_organizations.source import place

URL = "https://apps.irs.gov/pub/epostcard/data-download-epostcard.zip"
STATE = "0400000US46"
FIELDS = ["ein", "tax_year", "name", "gross_receipts_not_over_50000", "terminated",
          "period_begin", "period_end", "website", "officer_name", "officer_address_1",
          "officer_address_2", "officer_city", "officer_province", "officer_state",
          "officer_zip", "officer_country", "mailing_address_1", "mailing_address_2",
          "mailing_city", "mailing_province", "mailing_state", "mailing_zip",
          "mailing_country", "dba_1", "dba_2", "dba_3"]  # fmt: skip
NOT_A_NAME = {"", "NONE", "N/A", "NA"}


def _date(value: str) -> str | None:
    """mm-dd-yyyy as yyyy-mm-dd."""
    m, d, y = (value.split("-") + ["", "", ""])[:3]
    return f"{y}-{m}-{d}" if len(y) == 4 else None


def postcards(text: str):
    for row in csv.reader(io.StringIO(text), delimiter="|", quoting=csv.QUOTE_NONE):
        if len(row) < len(FIELDS) - 3 or not row[0].strip():
            continue  # the file starts with blank lines
        values = [v.strip() for v in row] + [""] * len(FIELDS)
        yield dict(zip(FIELDS, values, strict=False))


def items(fetcher) -> Found:
    content = fetcher.request("GET", URL, timeout=900).content
    store = getattr(fetcher, "store", None)
    if store is not None:
        store.save("data-download-epostcard.zip", content, URL)  # the latest only
    archive = zipfile.ZipFile(io.BytesIO(content))
    text = archive.read(archive.namelist()[0]).decode("utf-8", "replace")
    ours = eins(fetcher)
    out, seen, total = Found(), set(), 0
    for card in postcards(text):
        total += 1
        if card["ein"] not in ours and card["mailing_state"] != "SD":
            continue
        begin, end = _date(card["period_begin"]), _date(card["period_end"])
        key = f"{card['ein']} {begin} {end}"
        if key in seen:
            continue
        seen.add(key)
        data = {**card, "period_begin": begin, "period_end": end,
                "gross_receipts_not_over_50000": card["gross_receipts_not_over_50000"] == "T",
                "terminated": card["terminated"] == "T",
                "period_as_published": f"{card['period_begin']} to "
                                       f"{card['period_end']}"}  # fmt: skip
        city = place(card["mailing_city"].upper()) if card["mailing_state"] == "SD" else None
        out.append(Item(key=key, data=data, source_url=URL,
                        places=[STATE, *([city] if city else [])]))  # fmt: skip
    out.checks = {"postcards_in_file": total, "south_dakota": len(out)}
    return out


def mentions(record) -> list:
    d = record.data
    common = {"dataset": record.recordset.dataset, "record_key": str(record),
              "url": record.source_url,
              "context": {"ein": d["ein"], "tax_year": d["tax_year"]}}  # fmt: skip
    out = from_text(d["name"], field="name", role="organization", **common)
    for field in ("dba_1", "dba_2", "dba_3"):
        if d.get(field, "").upper() not in NOT_A_NAME:
            out += from_text(d[field], field=field, role="organization (other name)",
                             start=len(out), **common)  # fmt: skip
    if d.get("officer_name", "").upper() not in NOT_A_NAME:
        out += from_text(d["officer_name"], field="officer_name", role="principal officer",
                         order="given_first", start=len(out), **common)  # fmt: skip
    return out


SOURCE = Source(
    slug="irs-990n-postcards", items=items, mentions=mentions,
    name="Form 990-N e-Postcards (IRS)", dataset="irs-990n-postcards",
    publisher="Internal Revenue Service", base_url=URL,
    description="The annual e-Postcards of South Dakota's small tax-exempt organizations: "
                "principal officer, addresses, and whether the organization has ended.",
)  # fmt: skip
