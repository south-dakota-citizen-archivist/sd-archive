"""Read the Department of Revenue's annual Property Tax Statistical Reports (PDF).

Each report covers the taxes payable in its year (the 2024 report: the 2023 assessment,
taxes payable 2024). Its tables are read from the PDF's text layer (pdfium, which reads
the rotated landscape pages the right way up). Pages without text (scans in the
2018–2021 reports) are listed, not read.

Tables:
  schools        a school district's part of a county: valuations by class, the three
                 levies (all funds), the taxes
  municipalities each city or town (its part of a county): population, valuation, the
                 levies that apply to it, total tax rates by class, the taxes paid
  county_recaps  each county's recap: valuation, levy and tax by line
  valuations     each county's equalized valuations by class
  sales_ratios   each county's assessment-to-sales ratio, ag and non-ag
  ag_land        each county's ag valuation, acres and assessed dollars per acre
  effective      effective tax rates for a list of cities
"""

from __future__ import annotations

import re
from functools import cache

import sdplaces

MONEY = r"\$?\s*-?[\d,]+(?:\.\d+)?"
PAYABLE = re.compile(r"PAYABLE\s+(\d{4})|PAY\s+(\d{4})", re.I)


class LayoutChanged(Exception):
    pass


def pages(content: bytes) -> list[str]:
    """Each page's text, in reading order (rotated pages turned the right way)."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(content)
    try:
        return [doc[i].get_textpage().get_text_bounded().replace("\r", "") for i in range(len(doc))]
    finally:
        doc.close()


def number(text: str | None) -> float | int | None:
    if text is None:
        return None
    t = text.replace("$", "").replace(",", "").replace(" ", "").strip()
    if t in ("", "-", "--"):
        return None
    n = float(t)
    return int(n) if n.is_integer() and "." not in t else n


def _lines(text: str) -> list[str]:
    return [" ".join(line.split()) for line in text.splitlines() if line.strip()]


@cache
def _county_names() -> dict[str, str]:
    """Upper-case county name (and spacing variants: "MC COOK") → the name sdplaces uses."""
    out = {}
    for p in sdplaces.counties():
        name = p.short_name.upper()
        out[name] = p.short_name
        out[name.replace("MC", "MC ", 1) if name.startswith("MC") else name] = p.short_name
    return out


def county_name(text: str) -> str | None:
    return _county_names().get(" ".join(text.upper().split()))


def payable_year(text: str) -> int | None:
    m = PAYABLE.search(text)
    return int(m[1] or m[2]) if m else None


# --- schools ----------------------------------------------------------------------------

SCHOOL_ROW = re.compile(
    rf"^(?P<name>.+?) (?P<number>\d{{1,2}}-\d{{1,2}}) (?P<ag>{MONEY}) (?P<oo>{MONEY}) "
    rf"(?P<other>{MONEY}) (?P<central>{MONEY}) (?P<ag_levy>[\d.]+|-) (?P<oo_levy>[\d.]+|-) "
    rf"(?P<oth_levy>[\d.]+|-) (?P<taxes>{MONEY})$"
)


def schools(texts: list[str], file: str) -> tuple[list[dict], list[dict]]:
    """The school tables' rows, and the lines on their pages that weren't read."""
    out, unread, county = [], [], None
    for n, text in enumerate(texts, start=1):
        if "SCHOOL VALUATION, LEVY, AND TAXES" not in text.upper():
            continue
        year = payable_year(text)
        for line in _lines(text):
            if (c := county_name(line)) is not None:
                county = c
            elif m := SCHOOL_ROW.match(line):
                out.append({"payable_year": year, "county": county, "district": m["name"],
                            "district_number": m["number"],
                            "value_agricultural": number(m["ag"]),
                            "value_owner_occupied": number(m["oo"]),
                            "value_other_non_ag": number(m["other"]),
                            "value_centrally_assessed": number(m["central"]),
                            "levy_agricultural": number(m["ag_levy"]),
                            "levy_owner_occupied": number(m["oo_levy"]),
                            "levy_other": number(m["oth_levy"]), "taxes": number(m["taxes"]),
                            "file": file, "page": n})  # fmt: skip
            elif re.search(r"\$[\d,]+", line):
                unread.append({"file": file, "page": n, "line": line})
    return out, unread


# --- municipalities (rotated landscape pages) --------------------------------------------

MUNICIPAL_ROW = re.compile(
    r"^(?P<rest>.+?) (?P<sd>\d{1,2}-\d{1,2}) (?P<numbers>(?:(?:[\d,]+(?:\.\d+)?|-|\*) )+)"
    r"(?P<total>[\d,]+\.\d\d) ?\$?$"
)
# the levy columns after population and valuation, by whether the page has "School GEN AG"
MUNICIPAL_LEVIES = [
    "city_levy",
    "county_levy",
    "school_ag_levy",
    "school_oo_levy",
    "school_other_levy",
    "total_rate_ag",
    "total_rate_oo",
    "total_rate_other",
]
MUNICIPAL_LEVIES_GEN = MUNICIPAL_LEVIES[:2] + ["school_general_ag_levy"] + MUNICIPAL_LEVIES[2:]


def _split_place(rest: str) -> tuple[str, str] | None:
    """ "Buffalo Gap Custer" → ("Buffalo Gap", "Custer"): the county is the longest run of
    words at the end that names a county."""
    words = rest.split()
    for k in range(min(3, len(words) - 1), 0, -1):
        if (c := county_name(" ".join(words[-k:]))) is not None:
            return " ".join(words[:-k]), c
    return None


def municipalities(texts: list[str], file: str) -> tuple[list[dict], list[dict]]:
    out, unread = [], []
    for n, text in enumerate(texts, start=1):
        upper = " ".join(text.upper().split())
        if not upper.startswith("MUNICIPALITIES - VALUATIONS"):
            continue
        year = payable_year(upper)
        columns = MUNICIPAL_LEVIES_GEN if "SCHOOL GEN AG" in upper else MUNICIPAL_LEVIES
        for line in _lines(text):
            m = MUNICIPAL_ROW.match(line)
            place = _split_place(m["rest"]) if m else None
            numbers = m["numbers"].split() if m else []
            if not m or place is None or len(numbers) != 2 + len(columns):
                if re.search(r"\d{1,2}-\d{1,2} ", line):
                    unread.append({"file": file, "page": n, "line": line})
                continue
            out.append({"payable_year": year, "municipality": place[0], "county": place[1],
                        "school_district_number": m["sd"],
                        # "*": the city's part of another county (population on its main row)
                        "population": None if numbers[0] == "*" else number(numbers[0]),
                        "valuation": number(numbers[1]),
                        **{c: number(v) for c, v in zip(columns, numbers[2:], strict=True)},
                        "taxes_paid": number(m["total"]), "file": file, "page": n})  # fmt: skip
    return out, unread


# --- county recaps -----------------------------------------------------------------------

COUNTY_HEADING = re.compile(r"RECAP INFORMATION - TAXES PAYABLE (\d{4}) COUNTY:?\s*(.+)$", re.I)
RECAP_LINE = re.compile(
    r"^(?P<desc>[A-Za-z*].*?)(?: (?P<valuation>\$[\d,]+))?(?: (?P<levy>\d+\.\d+))?"
    r"(?: (?P<tax>\$[\d,]+(?:\.\d\d)?))$"
)


def county_recaps(texts: list[str], file: str) -> tuple[list[dict], int]:
    """Each county recap line with a valuation and a tax (and a levy where given). Lines
    with one amount, which could be either, aren't read; their number is returned."""
    out, skipped, county, year = [], 0, None, None
    for n, text in enumerate(texts, start=1):
        lines = _lines(text)
        heading = next((COUNTY_HEADING.search(x) for x in lines[:3] if COUNTY_HEADING.search(x)),
                       None)  # fmt: skip
        if heading:
            year = int(heading[1])
            name = heading[2].strip()
            county = "STATE SUMMARY" if name.upper() == "STATE SUMMARY" else county_name(name)
        elif county is None or not any("Corp" in x or "TOTAL" in x.upper() for x in lines):
            continue
        for line in lines:
            if COUNTY_HEADING.search(line):
                continue
            m = RECAP_LINE.match(line)
            if not m:
                continue
            if m["valuation"] is None and m["levy"] is None:
                skipped += 1  # one amount: a valuation or a tax?
                continue
            out.append({"payable_year": year, "county": county,
                        "description": m["desc"].strip(), "valuation": number(m["valuation"]),
                        "levy": number(m["levy"]), "tax": number(m["tax"]),
                        "file": file, "page": n})  # fmt: skip
    return out, skipped


# --- county tables: sales ratios, ag land --------------------------------------------------

# a ratio is a number or "-" (one is printed "52.3.", so a trailing period is allowed)
RATIO_PAIR = re.compile(
    r"([A-Z][A-Za-z .]+?) (-|\d+(?:\.\d+)?)\.? (-|\d+(?:\.\d+)?)\.?(?= [A-Z]|$)"
)


def sales_ratios(texts: list[str], file: str, report_year: int) -> list[dict]:
    """The assessment-to-sales ratio by county (percent), ag and non-ag, and the sales
    years the report says it's based on."""
    out = []
    for n, text in enumerate(texts, start=1):
        if "Sales to Assessment Ratio Analysis" not in text:
            continue
        sales = re.search(r"Sales for Years? ([\d, and]+)", text)
        for line in _lines(text):
            for name, ag, non_ag in RATIO_PAIR.findall(line):
                county = ("STATE SUMMARY" if name.strip().upper() == "STATE SUMMARY"
                          else county_name(name))  # fmt: skip
                if county:
                    out.append({"report_year": report_year, "county": county,
                                "sales_years": sales[1].strip() if sales else None,
                                "ratio_ag": number(ag), "ratio_non_ag": number(non_ag),
                                "file": file, "page": n})  # fmt: skip
        if out:
            break  # the table (the contents page names it too)
    return out


AG_LINE = re.compile(
    r"^(?P<name>[A-Z][A-Z .]+?) (?P<val>[\d,]+) (?P<acres>[\d,]+(?:\.\d+)?) "
    r"(?:\$ ?)?(?P<per>[\d,.]+)$"
)


def ag_land(texts: list[str], file: str) -> list[dict]:
    """Assessed ag valuation, acres and dollars per acre by county."""
    out = []
    for n, text in enumerate(texts, start=1):
        if not re.search(r"Assessed Dollar Per Acre - Agricultural Land", text):
            continue
        m = re.search(r"(\d{4}) Assessed Dollar Per Acre", text)
        for line in _lines(text):
            if (row := AG_LINE.match(line)) is None:
                continue
            name = row["name"].strip()
            county = "STATE SUMMARY" if name == "STATE SUMMARY" else county_name(name)
            if county:
                out.append(
                    {
                        "assessment_year": int(m[1]) if m else None,
                        "county": county,
                        "valuation": number(row["val"]),
                        "acres": number(row["acres"]),
                        "dollars_per_acre": number(row["per"]),
                        "file": file,
                        "page": n,
                    }
                )
    return out


def no_text(texts: list[str], file: str) -> list[int]:
    """Pages with (next to) no text: scans, or tables printed as pictures."""
    return [n for n, t in enumerate(texts, start=1) if len(t.strip()) < 60]
