"""Build tidy tables of South Dakota property tax levies from the files fetch.py saved.

    uv run python pipelines/property-tax/build.py

Output (in .cache/property-tax/build/, not committed):
  levies.parquet / .csv        one row per tax year × district × fund × property class, as
                               far as each recap breaks them down (2021 on)
  county_recap.parquet / .csv  one row per tax year × county × field of the county recap
                               (2018–2022)
  pt74.parquet / .csv          one row per tax year × district fund × taxing entity (2023 on)
  checks.json                  what doesn't add up, for review (also printed)

Places: each row's county GEOID, and for cities, towns and school districts the district's
own GEOID when exactly one place has its name (via sdplaces; school districts by their DOE
number where the file gives it). Every row names its file and spreadsheet row.

Checks:
  - a levy row's tax is its valuation × levy ÷ 1,000, allowing for the levy's rounding
    (each row's adds_up says whether it is)
  - the wide recaps' parts add up to their totals (school districts' classes to the Tax
    column, townships' funds to the totals)
  - one row per tax year × county × district × fund × class
  - which cities, towns and school districts have no place
  - the county recap: the values inside municipalities add up to CITY_VALUE, and the
    STATE SUMMARY row is the sum of the counties
  - PT-74 rows repeated with only the county ID different (left out of pt74, listed)
  - PT-74 summed by district fund against the recaps (how often valuation and tax agree),
    and class by class where a recap gives fund IDs and classes
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from functools import cache
from pathlib import Path

import pandas as pd
import parse  # pipelines/property-tax/parse.py (run as a script, so this folder is on the path)

import sdplaces

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
RAW = REPO / ".cache" / "property-tax" / "raw"
BUILD = REPO / ".cache" / "property-tax" / "build"
STATE_GEOID = "0400000US46"
CLASS_FIELD = re.compile(r"^(AG|OO|M|MOO|OT)_(IN|OUT)_(VAL|VA|TAX|TA)$")
RECAP_CLASS = {"AG": "agricultural", "OO": "owner_occupied", "M": "mobile_home",
               "MOO": "mobile_home_owner_occupied", "OT": "other"}  # fmt: skip
NOT_THE_PLACE = re.compile(r"\bR\.?S\.?D?\b|\bRS[- ]?\d|\bRSD\b|\bII\b", re.I)


# Misspelled county names in the files (the rows keep the name as published)
COUNTY_SPELLING = {"LINCON": "LINCOLN"}  # City_Town Recap-2021 and -2022, row 350


@cache
def county_geoid(name: str | None) -> str | None:
    if not isinstance(name, str) or name.upper() == parse.STATE_SUMMARY:
        return None
    try:
        name = COUNTY_SPELLING.get(name.upper(), name)
        return sdplaces.lookup("county", name, current=True).geoidfq
    except (sdplaces.UnknownPlace, sdplaces.AmbiguousPlace):
        return None


def _base(name: str) -> str:
    return re.sub(r"[\s-]+", "", re.sub(r"\s+\d{1,2}-\d{1,3}$", "", name)).lower()


@cache
def _school_names() -> dict[str, str | None]:
    """A school district's name without its number (normalized) → GEOID; None if several."""
    out: dict[str, str | None] = {}
    for p in sdplaces.all_places("school_district", include_historical=False):
        key = _base(p.short_name)
        out[key] = None if key in out else p.geoidfq
    return out


@cache
def district_geoid(kind: str, county: str | None, name: str | None,
                   code: str | None) -> str | None:  # fmt: skip
    """A city's, town's or school district's GEOID; None if no one place fits."""
    if not name:
        return None
    if kind == "school":
        if code and (m := re.fullmatch(r"(\d{1,2})-(\d{1,3})", code.strip())):
            try:
                return sdplaces.from_id("doe_district", f"{int(m[1]):02d}{int(m[2]):03d}").geoidfq
            except (sdplaces.UnknownPlace, sdplaces.AmbiguousPlace):
                pass
        return _school_names().get(_base(name))
    if kind == "city_town" and not NOT_THE_PLACE.search(name):
        try:
            return sdplaces.lookup("place", name).geoidfq
        except sdplaces.AmbiguousPlace:
            try:
                return sdplaces.lookup("place", name, within=county_geoid(county)).geoidfq
            except (sdplaces.UnknownPlace, sdplaces.AmbiguousPlace):
                return None
        except sdplaces.UnknownPlace:
            return None
    return None


def _text(v) -> str | None:
    return v if isinstance(v, str) else None


def _decimals(x: float) -> int:
    text = f"{round(float(x), 6):.6f}".rstrip("0")
    return len(text.split(".")[1]) if "." in text else 0


def off_by_more_than_rounding(valuation, levy, tax) -> bool:
    """Whether tax isn't valuation × levy ÷ 1,000, allowing for the levy's rounding (half a
    unit in its last published decimal) and cents."""
    if valuation is None or levy is None or tax is None:
        return False
    if pd.isna(valuation) or pd.isna(levy) or pd.isna(tax):
        return False
    allowed = valuation * 0.5 * 10 ** -_decimals(levy) / 1000 + 1
    return abs(valuation * levy / 1000 - tax) > allowed


def build_levies(files: dict, read) -> tuple[pd.DataFrame, dict]:
    checks: dict[str, list] = defaultdict(list)
    rows = []
    for name, f in sorted(files.items()):
        if f["kind"] in ("county", "pt74"):
            continue
        layout, found, parts = parse.levies(name, f["year"], read(name))
        for r in found:
            r["kind"] = f["kind"]
            r["source_url"] = f["url"]
        rows += found
        checks["parts don't add up to the total"] += parts
    df = pd.DataFrame(rows)
    df["county_geoid"] = df.county.map(county_geoid)
    df["district_geoid"] = [district_geoid(k, _text(c), _text(n), _text(s)) for k, c, n, s in
                            zip(df.kind, df.county, df.district, df.school_district_code,
                                strict=True)]  # fmt: skip
    for r in df[df.county_geoid.isna()].itertuples():
        checks["unknown counties"].append({"file": r.file, "row": r.row, "county": r.county})
    df["adds_up"] = [None if any(pd.isna(x) for x in (v, lv, t))
                     else not off_by_more_than_rounding(v, lv, t)
                     for v, lv, t in zip(df.valuation, df.levy, df.tax, strict=True)]  # fmt: skip
    for r in df[~df.tax_computed].itertuples():
        if r.adds_up is False:
            checks["tax isn't valuation × levy"].append(
                {
                    "file": r.file,
                    "row": r.row,
                    "district": r.district,
                    "fund": r.fund,
                    "class": r.property_class,
                    "valuation": r.valuation,
                    "levy": r.levy,
                    "tax": r.tax,
                    "valuation × levy": round(r.valuation * r.levy / 1000, 2),
                }
            )
    # a district can have several funds of one type (bond issues, opt-outs), told apart by
    # the fund ID in the files that give one
    key = ["assessment_year", "kind", "county", "district_type", "district", "fund", "fund_id",
           "property_class"]  # fmt: skip
    for _, g in df[df.duplicated(key, keep=False)].groupby(key, dropna=False):
        checks["more than one row for a district, fund and class"].append(
            {"file": g.file.iloc[0], "rows": g.row.tolist(), "district": g.district.iloc[0],
             "fund": g.fund.iloc[0], "class": g.property_class.iloc[0]})  # fmt: skip
    placed = df[df.kind.isin(["city_town", "school"])]
    for (kind, county, name), g in placed[placed.district_geoid.isna()].groupby(
            ["kind", "county", "district"]):  # fmt: skip
        checks["districts without a place"].append(
            {"kind": kind, "county": county, "district": name,
             "years": sorted(set(g.assessment_year))})  # fmt: skip
    cols = ["assessment_year", "payable_year", "kind", "county", "county_geoid", "district_type",
            "district",
            "district_geoid", "school_district_code", "fund", "property_class",
            "class_as_published", "valuation", "levy", "tax", "tax_computed", "adds_up", "discount",
            "tif",
            "district_id", "district_unique_id", "fund_id", "county_jurisdiction", "layout",
            "file", "row", "source_url"]  # fmt: skip
    df = df.reindex(columns=cols)
    df = df.sort_values(
        ["assessment_year", "kind", "county", "district", "fund", "property_class"],
        ascending=[False, True, True, True, True, True],
        na_position="first",
    )
    return df.reset_index(drop=True), dict(checks)


def build_county_recap(files: dict, read) -> tuple[pd.DataFrame, dict]:
    checks: dict[str, list] = defaultdict(list)
    rows = []
    for name, f in sorted(files.items()):
        if f["kind"] == "county":
            for r in parse.county_recap(name, f["year"], read(name)):
                rows.append({**r, "source_url": f["url"]})
    df = pd.DataFrame(rows)
    df["geography"] = df.county.map(lambda c: "state" if c == parse.STATE_SUMMARY else "county")
    df["county_geoid"] = df.county.map(lambda c: STATE_GEOID if c == parse.STATE_SUMMARY
                                       else county_geoid(c))  # fmt: skip
    parts = df.field.str.extract(CLASS_FIELD)
    df["property_class"] = parts[0].map(RECAP_CLASS)
    # IN and OUT: inside and outside corporate (city) limits, as the statistical reports'
    # county recaps label the same figures (AG_OUT_VAL = "AG PROPERTY - OUTSIDE CORP. LIMITS")
    df["inside_corporate_limits"] = parts[1].map({"IN": True, "OUT": False})
    df["measure"] = parts[2].map({"VAL": "valuation", "VA": "valuation", "TAX": "tax",
                                  "TA": "tax"})  # fmt: skip
    for r in df[df.county_geoid.isna()][["file", "county"]].drop_duplicates().itertuples():
        checks["unknown counties"].append({"file": r.file, "county": r.county})

    counties = df[df.geography == "county"]
    state = df[df.geography == "state"].set_index(["assessment_year", "field"]).value
    total = counties.groupby(["assessment_year", "field"]).value.sum()
    for k, v in state.items():
        if abs(total.get(k, 0) - v) > 1:
            checks["STATE SUMMARY ≠ the counties' sum"].append(
                {"assessment_year": k[0], "field": k[1], "state": v, "counties": total.get(k, 0)}
            )
    cols = [
        "assessment_year",
        "payable_year",
        "geography",
        "county",
        "county_geoid",
        "field",
        "property_class",
        "inside_corporate_limits",
        "measure",
        "value",
        "text",
        "file",
        "row",
        "source_url",
    ]
    df = df[cols].sort_values(["assessment_year", "geography", "county", "row"],
                              ascending=[False, False, True, True])  # fmt: skip
    return df.reset_index(drop=True), dict(checks)


VALUES = ["value_agricultural", "value_owner_occupied", "value_other", "value_utility",
          "value_mobile_home", "value_mobile_home_owner_occupied"]  # fmt: skip


def pt74_tax(df: pd.DataFrame) -> pd.Series:
    """Tax for each PT-74 row: each class's valuation at its rate (mobile homes at the
    owner-occupied or other rate, as the school recaps tax them)."""
    f = df.fillna({c: 0 for c in VALUES})
    return (f.rate_agricultural * f.value_agricultural
            + f.rate_owner_occupied * (f.value_owner_occupied + f.value_mobile_home_owner_occupied)
            + f.rate_other * (f.value_other + f.value_mobile_home)
            + f.rate_utility * f.value_utility) / 1000  # fmt: skip


def build_pt74(files: dict, read, levies: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    checks: dict[str, list] = defaultdict(list)
    rows = []
    for name, f in sorted(files.items()):
        if f["kind"] == "pt74":
            rows += [{**r, "source_url": f["url"]} for r in parse.pt74(name, read(name))]
    df = pd.DataFrame(rows)
    same = [c for c in df.columns if c not in ("county_id", "row")]
    repeated = df.duplicated(same, keep="first")
    for r in df[repeated].itertuples():
        checks["PT-74 rows repeated but for the county ID (left out)"].append(
            {"file": r.file, "row": r.row, "county_id": r.county_id, "district": r.district,
             "fund": r.fund, "taxing_entity_id": r.taxing_entity_id})  # fmt: skip
    df = df[~repeated].copy()
    df["county_geoid"] = df.county.map(county_geoid)
    df["tax"] = pt74_tax(df).round(2)

    # against the recaps, by county, district type, district and fund
    sums = (
        df.assign(valuation=df[VALUES].fillna(0).sum(axis=1))
        .groupby(["assessment_year", "county", "district_type", "district", "fund"])[
            ["valuation", "tax"]
        ]
        .sum()
    )
    recap = levies[levies.assessment_year.isin(set(df.assessment_year)) & levies.fund.notna()
                   & levies.property_class.isna()]  # fmt: skip
    for (year, kind), g in recap.groupby(["assessment_year", "kind"]):
        joined = g.merge(
            sums.add_suffix("_pt74"),
            how="left",
            right_index=True,
            left_on=["assessment_year", "county", "district_type", "district", "fund"],
        )
        found = joined.valuation_pt74.notna()
        checks["PT-74 against the recaps"].append({
            "assessment_year": int(year), "recap": kind, "recap_rows": len(g),
            "in_pt74": int(found.sum()),
            "valuation_equal": int(((joined.valuation - joined.valuation_pt74).abs() <= 1).sum()),
            "tax_within_half_a_percent": int(((joined.tax - joined.tax_pt74).abs()
                                              <= joined.tax.abs() * 0.005 + 5).sum()),
        })  # fmt: skip
    # the recaps' class valuations (where they give fund IDs and classes) against PT-74's
    by_class = df.groupby(["assessment_year", "county", "district", "fund_id"])[VALUES].sum()
    classed = levies[levies.fund_id.notna() & levies.property_class.notna()]
    for (year, county, district, fund_id), g in classed.groupby(
            ["assessment_year", "county", "district", "fund_id"]):  # fmt: skip
        if (year, county, district, fund_id) not in by_class.index:
            continue
        pt = by_class.loc[(year, county, district, fund_id)]
        recap = dict(zip(g.property_class, g.valuation, strict=True))
        wrong = sorted(k for k, v in recap.items() if abs(v - pt[f"value_{k}"]) > 1)
        if wrong:
            checks["recap class valuations ≠ PT-74"].append(
                {"assessment_year": int(year), "county": county, "district": district,
                 "fund_id": int(fund_id), "classes": wrong,
                 "recap": {k: recap[k] for k in wrong},
                 "pt74": {k: float(pt[f"value_{k}"]) for k in wrong}})  # fmt: skip
    cols = ["assessment_year", "payable_year", "county", "county_geoid", "district_type",
            "district", "fund",
            "taxing_entity_id", "rate_agricultural", "rate_owner_occupied", "rate_other",
            "rate_utility", *VALUES, "district_id", "fund_id", "county_id",
            "district_type_id", "fund_type_id", "alternate_id", "task_id", "file", "row",
            "source_url"]  # fmt: skip
    return df[cols].sort_values(["assessment_year", "county", "district_type", "district", "fund",
                                 "taxing_entity_id"],
                                ascending=[False, True, True, True, True, True]
                                ).reset_index(drop=True), dict(checks)  # fmt: skip


def _years_from_payable(df: pd.DataFrame) -> pd.DataFrame:
    df.insert(0, "assessment_year", df.payable_year - 1)
    return df


def _recap_lines(recaps: pd.DataFrame, checks: dict) -> list[str | None]:
    """Each recap row's line as the reports with text spell it ("AG PROPERTY - OUTSIDE CORP.
    LIMITS"), so a scan's reading ("AG PROPERTY- CUTSIDE CORP. LIMITS") lines up with other
    years: the same letters and digits, or else the one clearly closest spelling."""
    import difflib

    def key(s: str) -> str:
        return re.sub(r"[^A-Z0-9]", "", s.upper())

    text = recaps[recaps.read_from == "text"].description
    spelled = text.groupby(text.map(key)).agg(lambda s: s.value_counts().index[0]).to_dict()
    lines: dict[str, str | None] = {}
    for d in recaps.description.unique():
        if (k := key(d)) in spelled:
            lines[d] = spelled[k]
            continue
        ratios = sorted(((difflib.SequenceMatcher(None, k, c).ratio(), c) for c in spelled),
                        reverse=True)  # fmt: skip
        best, runner_up = ratios[0], ratios[1] if len(ratios) > 1 else (0, None)
        if best[0] >= 0.85 and best[0] - runner_up[0] >= 0.05:
            lines[d] = spelled[best[1]]
            checks["report recap lines matched by closest spelling"].append(
                {"read": d, "line": lines[d], "ratio": round(best[0], 3)})  # fmt: skip
        else:
            lines[d] = None
            checks["report recap lines not matched"].append(
                {"read": d, "closest": spelled[best[1]], "ratio": round(best[0], 3)})  # fmt: skip
    return [lines[d] for d in recaps.description]


def build_reports(reports: dict[int, str], read_report,
                  levies: pd.DataFrame, county_recap: pd.DataFrame
                  ) -> tuple[dict[str, pd.DataFrame], dict]:  # fmt: skip
    """The statistical reports' tables, with what wasn't read and what doesn't agree."""
    import ocr
    import report

    checks: dict[str, list] = defaultdict(list)
    found: dict[str, list] = defaultdict(list)
    for year, url in sorted(reports.items()):
        file = f"{year}.pdf"
        texts = report.pages(read_report(year))
        scanned = set()  # pages read by OCR (ocr.py), for rows' read_from
        for n in report.no_text(texts, file):
            if (page := ocr.stored(year, n)) is not None:
                texts[n - 1] = ocr.page_text(page)
                scanned.add(n)
            else:
                checks["report pages without text (not read)"].append({"file": file, "page": n})
        rows, unread = report.schools(texts, file)
        found["schools"] += rows
        checks["report lines not read"] += unread
        rows, unread = report.municipalities(texts, file)
        found["municipalities"] += rows
        checks["report lines not read"] += unread
        rows, unread = report.townships(texts, file)
        found["townships"] += rows
        checks["report lines not read"] += unread
        rows, skipped = report.county_recaps(texts, file)
        found["county_recaps"] += rows
        if skipped:
            checks["report recap lines with one amount (not read)"].append(
                {"file": file, "lines": skipped}
            )
        found["sales_ratios"] += report.sales_ratios(texts, file, year)
        found["ag_land"] += report.ag_land(texts, file)
        for name in (
            "schools",
            "municipalities",
            "townships",
            "county_recaps",
            "sales_ratios",
            "ag_land",
        ):
            for r in found[name]:
                if r["file"] == file:
                    r.setdefault("source_url", url)
                    r.setdefault("read_from", "ocr" if r.get("page") in scanned else "text")

    tables = {}
    for name, rows in found.items():
        df = pd.DataFrame(rows)
        if df.empty:
            continue
        if "payable_year" in df:
            df = _years_from_payable(df)
        elif "assessment_year" in df:
            df.insert(1, "payable_year", df.assessment_year + 1)
        df["county_geoid"] = df.county.map(
            lambda c: STATE_GEOID if c == parse.STATE_SUMMARY else county_geoid(c)
        )
        tables[f"report_{name}"] = df

    # counties missing from a county table
    for name in ("report_sales_ratios", "report_ag_land"):
        df = tables.get(name)
        if df is None:
            continue
        for file, g in df.groupby("file"):
            missing = sorted(set(c.short_name for c in sdplaces.counties()) - set(g.county))
            if missing:
                checks["counties missing from a report table"].append(
                    {"table": name, "file": file, "missing": missing}
                )
    recaps = tables.get("report_county_recaps")
    if recaps is not None:
        for file, g in recaps.groupby("file"):
            missing = sorted(set(c.short_name for c in sdplaces.counties()) - set(g.county))
            if missing:
                checks["counties missing from a report table"].append(
                    {"table": "report_county_recaps", "file": file, "missing": missing}
                )
        recaps["line"] = _recap_lines(recaps, checks)

    # schools: the report's method (ag levy × ag, owner-occupied levy × owner-occupied, the
    # other levy × the rest)
    schools = tables.get("report_schools")
    if schools is not None:
        f = schools.fillna(0)
        computed = (
            f.levy_agricultural * f.value_agricultural
            + f.levy_owner_occupied * f.value_owner_occupied
            + f.levy_other * (f.value_other_non_ag + f.value_centrally_assessed)
        ) / 1000
        off = (computed - f.taxes).abs() > (f.taxes.abs() * 0.001 + 5)
        for r, c in zip(schools[off].itertuples(), computed[off], strict=True):
            checks["report school taxes ≠ valuation × levies"].append(
                {"file": r.file, "page": r.page, "county": r.county, "district": r.district,
                 "taxes": r.taxes, "computed": round(c, 2)})  # fmt: skip
        schools["district_geoid"] = [
            district_geoid("school", None, d, n)
            for d, n in zip(schools.district, schools.district_number, strict=True)
        ]
        # against the Excel school recaps for the same payable year
        excel = levies[(levies.kind == "school") & (levies.property_class == "agricultural")
                       & (levies.fund.isna() | (levies.fund == "General"))]  # fmt: skip
        for year, g in schools.groupby("payable_year"):
            e = excel[excel.payable_year == year]
            if e.empty:
                continue
            have = set(zip(e.county.str.upper(), e.valuation.round(0), strict=True))
            same = sum((c.upper(), v) in have for c, v in zip(g.county, g.value_agricultural,
                                                              strict=True))  # fmt: skip
            checks["report schools against the Excel recaps"].append(
                {"payable_year": int(year), "report_rows": len(g), "excel_rows": len(e),
                 "same_ag_valuation": same})  # fmt: skip
        # each Excel school recap against every report year: which year its figures are
        # (School Recap-2021's match the 2019 report's, not 2021's)
        for file, e in excel.groupby("file"):
            have = set(zip(e.county.str.upper(), e.valuation.round(0), strict=True))
            by_year = {int(y): sum((c.upper(), v) in have
                                   for c, v in zip(g.county, g.value_agricultural, strict=True))
                       for y, g in schools.groupby("payable_year")}  # fmt: skip
            best = max(by_year, key=by_year.get)
            named = int(e.payable_year.iloc[0])
            if by_year[best] >= len(e) / 2 and best != named:  # most of its rows, not a few
                checks["Excel school recap matching another year's report"].append(
                    {"file": file, "payable_year_by_name": named, "best_report_year": best,
                     "same_ag_valuation_by_report_year": by_year})  # fmt: skip
    munis = tables.get("report_municipalities")
    if munis is not None:
        munis["municipality_geoid"] = [
            district_geoid("city_town", c, m, None)
            for c, m in zip(munis.county, munis.municipality, strict=True)
        ]
        # each total rate is the city's + the county's + the school's levy, or that and one
        # more levy, the same for every class (some counties' rates carry another); rates
        # that differ by class from the sums are a misread (or a misprint)
        munis["rates_check"] = [report.rates_check(r) for r in
                                munis.astype(object).where(munis.notna(), None)
                                .to_dict("records")]  # fmt: skip
        for r in munis[munis.rates_check == "doesn't add up"].itertuples():
            checks["report municipal total rates ≠ the levies' sums"].append(
                {"file": r.file, "page": r.page, "municipality": r.municipality,
                 "county": r.county, "read_from": r.read_from})  # fmt: skip
        if "read_as" not in munis:
            munis["read_as"] = None
        for r in munis[munis.read_as.fillna("") != ""].itertuples():
            checks["report municipal rows read with a blank or a lost point"].append(
                {"file": r.file, "page": r.page, "municipality": r.municipality,
                 "county": r.county, "read_as": r.read_as})  # fmt: skip
    towns = tables.get("report_townships")
    if towns is not None:
        computed = towns.taxable_value * towns.levy / 1000
        towns["adds_up"] = ((computed - towns.taxes).abs() <= (towns.taxes.abs() * 0.01)
                            .clip(lower=5)) | towns.levy.isna()  # fmt: skip
        for r in towns[~towns.adds_up].itertuples():
            checks["report township taxes ≠ taxable value × levy"].append(
                {"file": r.file, "page": r.page, "county": r.county, "township": r.township,
                 "taxable_value": r.taxable_value, "levy": r.levy, "taxes": r.taxes,
                 "read_from": r.read_from})  # fmt: skip
    if recaps is not None and not county_recap.empty:
        # the report's county recap against the Excel county recap (same payable year)
        ag_out = recaps[recaps.line == "AG PROPERTY - OUTSIDE CORP. LIMITS"]
        excel = county_recap[county_recap.field == "AG_OUT_VAL"]
        for year, g in ag_out.groupby("payable_year"):
            e = excel[excel.payable_year == year]
            if e.empty:
                continue
            have = dict(zip(e.county_geoid, e.value, strict=True))
            same = sum(have.get(c) == v for c, v in zip(g.county_geoid, g.valuation, strict=True))
            checks["report county recaps against the Excel county recaps"].append(
                {"payable_year": int(year), "counties": len(g), "same_ag_valuation": same}
            )
    return tables, dict(checks)


def build(files: dict, read, reports: dict | None = None,
          read_report=None) -> tuple[dict[str, pd.DataFrame], dict]:  # fmt: skip
    levies, c1 = build_levies(files, read)
    county, c2 = build_county_recap(files, read)
    pt74, c3 = build_pt74(files, read, levies)
    tables = {"levies": levies, "county_recap": county, "pt74": pt74}
    c4 = {}
    if reports:
        more, c4 = build_reports(reports, read_report, levies, county)
        tables.update(more)
    return tables, {**c1, **c2, **c3, **c4}


def main() -> None:
    files = json.loads((RAW / "files.json").read_text())
    reports_file = RAW / "statistical-reports.json"
    reports = ({int(k): v for k, v in json.loads(reports_file.read_text()).items()}
               if reports_file.exists() else {})  # fmt: skip
    tables, checks = build(
        files,
        lambda name: (RAW / "files" / name).read_bytes(),
        reports,
        lambda year: (RAW / "statistical-reports" / f"{year}.pdf").read_bytes(),
    )
    BUILD.mkdir(parents=True, exist_ok=True)
    for name, df in tables.items():
        df.to_parquet(BUILD / f"{name}.parquet", index=False)
        df.to_csv(BUILD / f"{name}.csv", index=False)
        years = df.payable_year if "payable_year" in df else df.report_year
        print(f"{name}: {len(df):,} rows (payable {years.min()}–{years.max()})")
    (BUILD / "checks.json").write_text(json.dumps(checks, indent=1, default=str))
    for name, found in checks.items():
        print(f"  {name}: {len(found)}")
        for item in found[:4]:
            print(f"    {item}")


if __name__ == "__main__":
    main()
