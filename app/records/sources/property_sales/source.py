"""Property sales reported for the sales ratio study, from the Department of Revenue's
property tax data page (sdproptax.info/DataLink/Data): one record per sale, for every county,
with the parcel(s), the instrument date, the sale price, the assessed values, the
sales-to-assessment ratio and whether the sale was rejected from the study (and why).

The Department publishes a file for each equalization year (sales from November of one year
through October of the next count toward the year after). The files have two layouts:

- 2021 and 2022: county name, the county's sequence number, parcel number, legal
  description, acres, PLSS township and range, school district, deed type, transfer fee and
  the county's remarks. Keyed by year, county and sequence number.
- 2023 on: the county's FIPS code, the state system's sale ID, recording date, document
  number and the state parcel ID(s), several for a sale of more than one parcel, and the
  reason a rejected sale was rejected. Keyed by year and sale ID.

Each record keeps the row as published (`published`) next to the parsed fields. The
published ratio is computed differently in the two layouts: sale price ÷ assessed value in
2021–2022, assessed value ÷ sale price from 2023 (`ratio_is` says which). A ratio of -99 in
the 2023-on files means no ratio was computed (no sale price), so it's stored as None.
`assessment_ratio` is assessed value ÷ sale price for every year, when both are above zero.
The files name no buyers or sellers. Tied to the county, and in 2021–2022 to the school
district."""

from __future__ import annotations

import csv
import io
import re
from urllib.parse import unquote, urljoin

import sdplaces
from records.sources import Found, Item, Source

PAGE = "https://sdproptax.info/DataLink/Data"
SALES_FILE = re.compile(r'href="(?P<href>[^"]*filename=(?P<year>\d{4})_SD_SALES\.csv)"', re.I)
OLD = {"Sequence", "County", "Class", "Ag Flag", "Instrument Date", "Acres", "Selling Price",
       "Building Value", "Land Value", "Assessed Value", "Ratio", "Range", "Township",
       "School District", "Parcel No.", "Legal", "Transfer Fee", "Doc Type", "Rejected",
       "Remarks", "Year"}  # fmt: skip
NEW = {"Id", "JurisdictionName", "JurisdictionFIPS", "EqYear", "Sequence", "Type",
       "Classification", "DOEAgFlag", "InstrumentDate", "RecordingDate", "Document", "StatePID",
       "SalePrice", "BldValue", "LandValue", "TotalValue", "Ratio", "RejectStatus"}  # fmt: skip
NO_RATIO = -99.0
PRICE_OVER_ASSESSED = "sale price / assessed value"
ASSESSED_OVER_PRICE = "assessed value / sale price"


class LayoutChanged(Exception):
    pass


def sales_files(html: str) -> dict[int, str]:
    """Equalization year → the sales file's URL, from the data page."""
    return {int(m["year"]): urljoin(PAGE, m["href"].replace("&amp;", "&"))
            for m in SALES_FILE.finditer(html)}  # fmt: skip


def _value(v: str) -> str | None:
    v = v.strip()
    return None if v in ("", "NULL") else v


def _number(v: str) -> int | float | None:
    v = _value(v)
    if v is None:
        return None
    n = float(v.replace(",", ""))
    return int(n) if n.is_integer() and "." not in v else n


def _parcels(v: str | None) -> list[str]:
    """A sale's parcel numbers: several are separated by commas (or "|" in 2021–2022)."""
    return [p.strip() for p in re.split(r"[|,]", v or "") if _value(p)]


def _county(name: str | None = None, fips: str | None = None) -> str | None:
    try:
        if fips:
            return sdplaces.get(f"0500000US{fips}", "county").geoidfq
        return sdplaces.lookup("county", name).geoidfq
    except (sdplaces.UnknownPlace, sdplaces.AmbiguousPlace):
        return None


def _school_district(code: str) -> str | None:
    """A DOE district number as the files write it ("06-1") → the district's GEOID."""
    m = re.fullmatch(r"(\d{1,2})-(\d{1,3})", code.strip())
    if not m:
        return None  # "OTHER"
    try:
        return sdplaces.from_id("doe_district", f"{int(m[1]):02d}{int(m[2]):03d}").geoidfq
    except (sdplaces.UnknownPlace, sdplaces.AmbiguousPlace):
        return None


def rows(text: str) -> tuple[str, list[dict]]:
    """The file's layout ("2021" or "2023") and its rows as published."""
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    header = set(reader.fieldnames or [])
    if header == OLD:
        layout = "2021"
    elif header == NEW:
        layout = "2023"
    else:
        raise LayoutChanged(
            f"unknown columns: {sorted(header ^ (OLD if 'County' in header else NEW))}"
        )
    return layout, list(reader)


def sale(row: dict, year: int, layout: str) -> tuple[str, dict, list[str]]:
    """A row as (key, data, places)."""
    if layout == "2021":
        county = _county(name=row["County"])
        district = _school_district(row["School District"])
        rejected = row["Rejected"].strip() == "1"
        data = {
            "county": row["County"].strip(), "sequence": _value(row["Sequence"]),
            "classification": _value(row["Class"]), "ag_flag": _value(row["Ag Flag"]),
            "instrument_date": _value(row["Instrument Date"]), "recording_date": None,
            "document": None, "parcels": _parcels(row["Parcel No."]),
            "sale_price": _number(row["Selling Price"]),
            "building_value": _number(row["Building Value"]),
            "land_value": _number(row["Land Value"]),
            "assessed_value": _number(row["Assessed Value"]),
            "ratio": _number(row["Ratio"]), "ratio_is": PRICE_OVER_ASSESSED,
            "rejected": rejected, "reject_reason": None,
            "remarks": " ".join((row["Remarks"] or "").split()) or None,
            "acres": _number(row["Acres"]), "plss_township": _value(row["Township"]),
            "plss_range": _value(row["Range"]), "school_district": _value(row["School District"]),
            "legal_description": _value(row["Legal"]), "doc_type": _value(row["Doc Type"]),
            "transfer_fee": _number(row["Transfer Fee"]),
        }  # fmt: skip
        key = f"{year} {row['County'].strip()} {row['Sequence'].strip()}"
        places = [p for p in (county, district) if p]
    else:
        county = _county(fips=row["JurisdictionFIPS"].strip())
        ratio = _number(row["Ratio"])
        reason = _value(row["RejectStatus"])
        data = {
            "county": row["JurisdictionName"].strip(), "sale_id": row["Id"].strip(),
            "sequence": _value(row["Sequence"]), "type": _value(row["Type"]),
            "classification": _value(row["Classification"]), "ag_flag": _value(row["DOEAgFlag"]),
            "instrument_date": _value(row["InstrumentDate"]),
            "recording_date": _value(row["RecordingDate"]), "document": _value(row["Document"]),
            "parcels": _parcels(row["StatePID"]),
            "sale_price": _number(row["SalePrice"]), "building_value": _number(row["BldValue"]),
            "land_value": _number(row["LandValue"]), "assessed_value": _number(row["TotalValue"]),
            "ratio": None if ratio == NO_RATIO else ratio, "ratio_is": ASSESSED_OVER_PRICE,
            "rejected": reason is not None, "reject_reason": reason,
        }  # fmt: skip
        key = f"{year} {row['Id'].strip()}"
        places = [county] if county else []
    price, assessed = data["sale_price"], data["assessed_value"]
    data["assessment_ratio"] = (round(assessed / price, 4)
                                if price and assessed and price > 0 and assessed > 0
                                else None)  # fmt: skip
    return key, {"equalization_year": year, **data, "layout": layout,
                 "published": row}, places  # fmt: skip


def items(fetcher) -> Found:
    html = fetcher.request("GET", PAGE).text
    files = sales_files(html)
    if not files:
        raise LayoutChanged("no sales files on the data page")
    store = getattr(fetcher, "store", None)
    out, checks, seen = Found(), {}, set()
    for year, url in sorted(files.items()):
        content = fetcher.request("GET", url, headers={"Referer": PAGE}, timeout=300).content
        if store is not None:
            store.save(unquote(url.rsplit("=", 1)[-1]), content, url)
        layout, published = rows(content.decode("utf-8-sig"))
        unplaced = 0
        for row in published:
            key, data, places = sale(row, year, layout)
            if key in seen:
                raise LayoutChanged(f"{key} twice: the key isn't unique")
            seen.add(key)
            unplaced += not places
            out.append(Item(key=key, data=data, source_url=url, places=places))
        checks[str(year)] = {"rows": len(published), "layout": layout,
                             "without_a_county": unplaced}  # fmt: skip
    out.checks = {"files": checks, "sales": len(out)}
    return out


SOURCE = Source(
    slug="property-sales", items=items,
    name="Property sales (sales ratio study)", dataset="property-sales",
    publisher="South Dakota Department of Revenue", base_url=PAGE,
    description="Every property sale counties report for the state's sales ratio study: "
                "parcel, date, price, assessed value, ratio, and whether (and why) the sale "
                "was rejected from the study.",
)  # fmt: skip
