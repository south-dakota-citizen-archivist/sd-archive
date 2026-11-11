"""Read the Department of Revenue's property tax files into rows, one reader per layout.

Each file's layout is recognized by its header row; an unknown header raises LayoutChanged
rather than guessing which column is which. Values are kept as published (blank → None).

Levy recaps (City/Town, Township, Road District, Other District, School District) become
levy rows: one per district × fund × property class, as far as the file breaks them down
(an older file without funds or classes gives fund or class None). County recaps and PT-74
files have their own shapes.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Iterator

STATE_SUMMARY = "STATE SUMMARY"

# Property classes. The 2023–2024 school recaps spell them out; the other files use codes.
CLASSES = {
    "Agricultural": "agricultural", "AG": "agricultural", "ag": "agricultural",
    "Owner Occupied": "owner_occupied", "OO": "owner_occupied", "oo": "owner_occupied",
    "Other": "other", "OT": "other", "oth": "other",
    "Utility": "utility", "UT": "utility", "util": "utility",
    "Mobile Home": "mobile_home", "M": "mobile_home", "m": "mobile_home",
    "Mobile Home Owner Occupied": "mobile_home_owner_occupied",
    "M-OO": "mobile_home_owner_occupied", "m_oo": "mobile_home_owner_occupied",
}  # fmt: skip

DISTRICT_FUND = (
    "County",
    "TaxYear",
    "District_Type",
    "Name",
    "FundTypeName",
    "Valuation",
    "TaxLevy",
    "TaxAmount",
    "CountyId",
    "DistrictId",
    "DistrictUniqueId",
    "FundId",
)
DISTRICT_FUND_CLASS = (*DISTRICT_FUND[:5], "ClassCode", *DISTRICT_FUND[5:])
SCHOOL_CLASS = (
    "Tax Year",
    "County",
    "School District",
    "Classification",
    "Valuation",
    "Tax Levy",
    "Tax Amount",
)
TOWNSHIP_TOTAL = ("Tax Year", "County", "Township", "Valuation", "Tax Levy", "Tax Amount")
CITY_2021 = ("COUNTY", "TYPE", "NAME", "SCH_DIST", "TAX_VAL", "MILL_LEVY", "TAXES")
ROAD_2021 = ("COUNTY", "type", "name", "TAX_VAL", "MILL_LEVY", "TAXES", "% GROWTH")
TOWNSHIP_2021 = ("COUNTY", "TYPE", "NAME", "TAX_VAL", "MILL_LEVY", "TAXES")
TOWNSHIP_2022 = (
    "COUNTY",
    "TYPE",
    "NAME",
    "TAX_VAL",
    "BASE_LEVY",
    "OPT_OUT_LEVY",
    "RB_LEVY",
    "BASE_TAXES",
    "OPT_OUT_TAXES",
    "RB_TAXES",
    "TOTAL_MILL_LEVY",
    "TOTAL_TAXES",
)
_SCHOOL_WIDE = (
    "county",
    "sch_dist",
    "sch_name",
    "co_jur",
    "ag_val",
    "oo_val",
    "m_val",
    "m_oo_val",
    "oth_val",
    "util_val",
    "ag_disc",
    "oo_disc",
)
SCHOOL_2021 = (
    *_SCHOOL_WIDE,
    "m_disc",
    "m_oo_disc",
    "oth_disc",
    "ag_tif",
    "oo_tif",
    "m_tif",
    "m_oo_tif",
    "oth_tif",
    "util_tif",
    "Ag_Levy",
    "Oth_Levy",
    "OO_Levy",
    "Tax",
)
SCHOOL_2022 = (
    *_SCHOOL_WIDE,
    "oth_disc",
    "ag_tif",
    "oo_tif",
    "m_tif",
    "m_oo_tif",
    "oth_tif",
    "util_tif",
    "Ag_Levy",
    "OO_Levy",
    "Oth_Levy",
    "Tax",
    "Field24",
    "ag taxes",
    "oo taxes",
    "m taxes",
    "moo taxes",
    "oth taxes",
    "util taxes",
)
WIDE_CLASSES = ("ag", "oo", "m", "m_oo", "oth", "util")
# Which of a wide school recap's three levies each class pays (owner-occupied homes, mobile or
# not, pay the owner-occupied levy; mobile homes, utilities and other property the other).
WIDE_LEVY = {"ag": "Ag_Levy", "oo": "OO_Levy", "m_oo": "OO_Levy", "m": "Oth_Levy",
             "oth": "Oth_Levy", "util": "Oth_Levy"}  # fmt: skip
WIDE_TAXES = {"ag": "ag taxes", "oo": "oo taxes", "m": "m taxes", "m_oo": "moo taxes",
              "oth": "oth taxes", "util": "util taxes"}  # fmt: skip
PT74 = ("CountyId", "County", "DistrictId", "DistrictTypeId", "DistrictType", "DistrictName",
        "AlternateId", "FundType", "FundTypeId", "TaskId", "FundId", "TaxingEntityId", "RateAg",
        "RateOO", "RateO", "RateUtility", "ValueAg", "ValueOO", "ValueO", "ValueUtility",
        "ValueM", "ValueMOO", "TaxYear")  # fmt: skip


class LayoutChanged(Exception):
    pass


def _clean(v):
    if isinstance(v, str):
        v = " ".join(re.sub(r"_x000d_", " ", v, flags=re.I).split())  # Excel's escaped CR
        return v or None
    return v


def sheet(content: bytes) -> tuple[tuple, Iterator[tuple[int, dict]]]:
    """A workbook's first sheet: its header, and (spreadsheet row number, row) for each
    non-empty row."""
    import openpyxl

    ws = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True).worksheets[0]
    rows = ws.iter_rows(values_only=True)
    header = tuple(_clean(h) for h in next(rows))
    while header and header[-1] is None:
        header = header[:-1]

    def body():
        for n, row in enumerate(rows, start=2):
            values = [_clean(v) for v in row[: len(header)]]
            if any(v is not None for v in values):
                yield n, dict(zip(header, values, strict=False))

    return header, body()


def _levy(file: str, n: int, layout: str, year, county, district_type, district, fund,
          klass, valuation, levy, tax, **extra) -> dict:  # fmt: skip
    return {"tax_year": int(year), "county": county, "district_type": district_type,
            "district": district, "fund": fund,
            "property_class": CLASSES[klass] if klass is not None else None,
            "class_as_published": klass, "valuation": valuation, "levy": levy, "tax": tax,
            "tax_computed": False, "school_district_code": None, "discount": None,
            "tif": None, "district_id": None, "district_unique_id": None, "fund_id": None,
            **extra, "file": file, "row": n, "layout": layout}  # fmt: skip


def levies(file: str, year: int, content: bytes) -> tuple[str, list[dict], list[dict]]:
    """A levy recap's layout, its levy rows, and what doesn't add up within a row."""
    header, body = sheet(content)
    out, checks = [], []
    if header in (DISTRICT_FUND, DISTRICT_FUND_CLASS):
        layout = "district-fund-class" if header == DISTRICT_FUND_CLASS else "district-fund"
        for n, r in body:
            out.append(_levy(file, n, layout, r["TaxYear"], r["County"], r["District_Type"],
                             r["Name"], r["FundTypeName"], r.get("ClassCode"), r["Valuation"],
                             r["TaxLevy"], r["TaxAmount"], district_id=r["DistrictId"],
                             district_unique_id=r["DistrictUniqueId"],
                             fund_id=r["FundId"]))  # fmt: skip
    elif header == SCHOOL_CLASS:
        layout = "school-class"
        for n, r in body:
            out.append(_levy(file, n, layout, r["Tax Year"], r["County"], "School",
                             r["School District"], None, r["Classification"], r["Valuation"],
                             r["Tax Levy"], r["Tax Amount"]))  # fmt: skip
    elif header == TOWNSHIP_TOTAL:
        layout = "township-total"
        for n, r in body:
            out.append(_levy(file, n, layout, r["Tax Year"], r["County"], "Township",
                             r["Township"], None, None, r["Valuation"], r["Tax Levy"],
                             r["Tax Amount"]))  # fmt: skip
    elif header in (CITY_2021, ROAD_2021, TOWNSHIP_2021):
        layout = {CITY_2021: "city-2021", ROAD_2021: "road-2021",
                  TOWNSHIP_2021: "township-2021"}[header]  # fmt: skip
        for n, r in body:
            county = r.get("COUNTY")
            out.append(_levy(file, n, layout, year, county, r.get("TYPE") or r.get("type"),
                             r.get("NAME") or r.get("name"), None, None, r["TAX_VAL"],
                             r["MILL_LEVY"], r["TAXES"],
                             school_district_code=r.get("SCH_DIST")))  # fmt: skip
    elif header == TOWNSHIP_2022:
        layout = "township-2022"
        for n, r in body:
            parts = [(f, r[f"{f}_LEVY"], r[f"{f}_TAXES"]) for f in ("BASE", "OPT_OUT", "RB")]
            for fund, levy, tax in parts:
                out.append(_levy(file, n, layout, year, r["COUNTY"], r["TYPE"], r["NAME"],
                                 fund, None, r["TAX_VAL"], levy, tax))  # fmt: skip
            total_levy = sum(p[1] or 0 for p in parts)
            total_tax = sum(p[2] or 0 for p in parts)
            if (abs(total_levy - (r["TOTAL_MILL_LEVY"] or 0)) > 0.0015
                    or abs(total_tax - (r["TOTAL_TAXES"] or 0)) > 1):  # fmt: skip
                checks.append({"file": file, "row": n, "district": r["NAME"],
                               "funds": [total_levy, total_tax],
                               "total": [r["TOTAL_MILL_LEVY"], r["TOTAL_TAXES"]]})  # fmt: skip
    elif header in (SCHOOL_2021, SCHOOL_2022):
        layout = "school-2021" if header == SCHOOL_2021 else "school-2022"
        levy_of = dict(WIDE_LEVY)
        if layout == "school-2021":
            # The 2021 file's OO_Levy and Oth_Levy headings are the wrong way round: every
            # row's Tax is ag × Ag_Levy + owner-occupied × "Oth_Levy" + the rest × "OO_Levy"
            # (checked in build.py), and every row's "OO_Levy" is the higher.
            levy_of = {c: {"OO_Levy": "Oth_Levy", "Oth_Levy": "OO_Levy"}.get(f, f)
                       for c, f in WIDE_LEVY.items()}  # fmt: skip
        for n, r in body:
            computed = 0.0
            for c in WIDE_CLASSES:
                valuation, levy = r[f"{c}_val"], r[levy_of[c]]
                tax = r.get(WIDE_TAXES[c]) if layout == "school-2022" else None
                is_computed = tax is None and valuation is not None and levy is not None
                if is_computed:
                    tax = round(valuation * levy / 1000, 2)
                computed += tax or 0
                out.append(_levy(file, n, layout, year, r["county"], "School", r["sch_name"],
                                 None, c, valuation, levy, tax, tax_computed=is_computed,
                                 school_district_code=r["sch_dist"],
                                 discount=r.get(f"{c}_disc"), tif=r.get(f"{c}_tif"),
                                 county_jurisdiction=r["co_jur"]))  # fmt: skip
            if r["Tax"] is not None and abs(computed - r["Tax"]) > 1:
                checks.append({"file": file, "row": n, "district": r["sch_name"],
                               "classes": round(computed, 2), "Tax": r["Tax"]})  # fmt: skip
    else:
        raise LayoutChanged(f"{file}: unknown header {header}")
    empty = ("district", "valuation", "levy", "tax")  # a row with nothing but a type in it
    out = [r for r in out if any(r[k] is not None for k in empty)]
    return layout, out, checks


def county_recap(file: str, year: int, content: bytes) -> list[dict]:
    """A county recap (one row per county, a column per figure) as one row per county ×
    field, the field named as published."""
    header, body = sheet(content)
    if not header or header[0] != "COUNTY" or len(header) != 89:
        raise LayoutChanged(f"{file}: unknown header {header[:5]}… ({len(header)} columns)")
    out = []
    for n, r in body:
        county = r["COUNTY"]
        if isinstance(county, str) and len(county.split()) % 2 == 0:
            # "MARSHALL_x000d_ Marshall": a name typed in twice, on two lines of the cell
            half = len(county.split()) // 2
            if " ".join(county.split()[:half]).upper() == " ".join(county.split()[half:]).upper():
                county = " ".join(county.split()[:half])
        for field in header[1:]:
            v = r[field]
            if v is None:
                continue
            out.append({"tax_year": year, "county": county, "field": field,
                        "value": v if isinstance(v, int | float) else None,
                        "text": v if isinstance(v, str) else None,
                        "file": file, "row": n})  # fmt: skip
    return out


def pt74(file: str, content: bytes) -> list[dict]:
    """A PT-74 file: one row per district fund × taxing entity (an area taxed by one
    combination of districts), with its rates and valuations by class."""
    reader = csv.reader(io.StringIO(content.decode("utf-8-sig")))
    header = tuple(next(reader))
    if header != PT74:
        raise LayoutChanged(f"{file}: unknown header {header}")
    out = []
    for n, values in enumerate(reader, start=2):
        if not any(values):
            continue
        r = dict(zip(header, values, strict=True))

        def num(k, r=r):
            return float(r[k]) if r[k] not in ("", "NULL") else None

        out.append({
            "tax_year": int(r["TaxYear"]), "county": r["County"],
            "district_type": r["DistrictType"], "district": " ".join(r["DistrictName"].split()),
            "fund": r["FundType"], "taxing_entity_id": int(r["TaxingEntityId"]),
            "district_id": int(r["DistrictId"]), "fund_id": int(r["FundId"]),
            "county_id": int(r["CountyId"]), "district_type_id": int(r["DistrictTypeId"]),
            "fund_type_id": int(r["FundTypeId"]), "alternate_id": r["AlternateId"] or None,
            "task_id": int(r["TaskId"]),
            "rate_agricultural": num("RateAg"), "rate_owner_occupied": num("RateOO"),
            "rate_other": num("RateO"), "rate_utility": num("RateUtility"),
            "value_agricultural": num("ValueAg"), "value_owner_occupied": num("ValueOO"),
            "value_other": num("ValueO"), "value_utility": num("ValueUtility"),
            "value_mobile_home": num("ValueM"),
            "value_mobile_home_owner_occupied": num("ValueMOO"),
            "file": file, "row": n,
        })  # fmt: skip
    return out
