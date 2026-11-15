"""Read the Department of Revenue's annual Property Tax Statistical Reports (PDF).

Each report covers the taxes payable in its year (the 2024 report: the 2023 assessment,
taxes payable 2024). Its tables are read from the PDF's text layer (pdfium, which reads
the rotated landscape pages the right way up). Pages without text (scans in the
2018–2021 reports) are read from their OCR text (ocr.py) where it's been made; each row says
which (`read_from`).

Tables:
  schools        a school district's part of a county: valuations by class, the three
                 levies (all funds), the taxes
  municipalities each city or town (its part of a county): population, valuation, the
                 levies that apply to it, total tax rates by class, the taxes paid
  townships      each township's taxable value, mill levy and taxes, by county
  county_recaps  each county's recap: valuation, levy and tax by line
  valuations     each county's equalized valuations by class
  sales_ratios   each county's assessment-to-sales ratio, ag and non-ag
  ag_land        each county's ag valuation, acres and assessed dollars per acre
  effective      effective tax rates for a list of cities
"""

from __future__ import annotations

import re
import statistics
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
    r"(?:\$ ?)?(?P<total>[\d,]+\.\d\d) ?\$?$"  # the dollar sign before (scans) or after
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
        # "School GEN AG" (a scan's header can split it over two lines: "SCHOOL … GEN AG")
        header = upper[:600]
        columns = MUNICIPAL_LEVIES_GEN if re.search(r"\bGEN AG\b", header) else MUNICIPAL_LEVIES
        if "RATE UTIL" in upper:  # 2019–2021: a total rate for utility property too
            columns = [*columns, "total_rate_utility"]
        for line in _lines(text):
            # a scan's asterisk can read as another mark ("×", "文"), alone between spaces
            line = re.sub(r"(?<= )[^\x00-\x7f](?= )", "*", line)
            m = MUNICIPAL_ROW.match(line)
            place = _split_place(m["rest"]) if m else None
            numbers = m["numbers"].split() if m else []
            row = _municipal_numbers(numbers, columns, number(m["total"])) if place else None
            if row is None:
                if re.search(r"\d{1,2}-\d{1,2} ", line):
                    unread.append({"file": file, "page": n, "line": line})
                continue
            # stray digits a scan put before the name ("17 16 Iroquois")
            name = re.sub(r"^(?:\d+ )+(?=\D)", "", place[0])
            out.append({"payable_year": year, "municipality": name, "county": place[1],
                        "school_district_number": m["sd"], **row,
                        "taxes_paid": number(m["total"]), "file": file, "page": n})  # fmt: skip
    return out, unread


def _rates_add_up(row: dict) -> bool:
    """Each total rate is the city's, the county's and the school's levy for that class."""
    base = (row.get("city_levy") or 0) + (row.get("county_levy") or 0)
    pairs = (("total_rate_ag", "school_ag_levy"), ("total_rate_oo", "school_oo_levy"),
             ("total_rate_other", "school_other_levy"))  # fmt: skip
    checked = [(row[t], base + (row[s] or 0)) for t, s in pairs if row.get(t) is not None]
    return len(checked) >= 2 and all(abs(t - c) < 0.0015 for t, c in checked)


def rates_check(row: dict) -> str:
    """How a row's total rates compare with its levies: "adds up" (each total rate is the
    city's, the county's and the school's levy for that class; a blank levy is none),
    "another levy of N" (each is that and the same N more: some counties' rates carry
    another levy), "doesn't add up", or "" (a levy left blank because its point was lost)."""
    if _rates_add_up(row):
        return "adds up"
    base = (row.get("city_levy") or 0) + (row.get("county_levy") or 0)
    pairs = (("total_rate_ag", "school_ag_levy"), ("total_rate_oo", "school_oo_levy"),
             ("total_rate_other", "school_other_levy"))  # fmt: skip
    extra = [row[t] - base - row[s] for t, s in pairs
             if row.get(t) is not None and row.get(s) is not None]  # fmt: skip
    if "left blank" in (row.get("read_as") or "") or len(extra) < 2:
        return ""  # a levy that couldn't be read is in the difference
    if max(extra) - min(extra) < 0.0015:
        return f"another levy of {statistics.mean(extra):.3f}"
    return "doesn't add up"


def _taxes_fit(row: dict, taxes: float | None) -> bool:
    """The taxes paid over the valuation (per thousand) is between the lowest and highest
    total rate: the taxes are each class's valuation times its rate."""
    rates = [row[c] for c in ("total_rate_ag", "total_rate_oo", "total_rate_other")
             if row.get(c) is not None]  # fmt: skip
    if not rates or not row.get("valuation") or taxes is None:
        return False
    per_thousand = taxes / row["valuation"] * 1000
    return min(rates) * 0.99 - 0.01 <= per_thousand <= max(rates) * 1.01 + 0.01


# the levies and rates the total rates' sums check (_rates_add_up)
CHECKED = {
    "city_levy",
    "county_levy",
    "school_ag_levy",
    "school_oo_levy",
    "school_other_levy",
    "total_rate_ag",
    "total_rate_oo",
    "total_rate_other",
}


def _municipal_numbers(numbers: list[str], columns: list[str], taxes) -> dict | None:
    """A row's population, valuation and levies.

    A levy of 100 or more is a decimal point a scan lost ("2645": no levy is that high; a
    whole one, "19", is the report leaving off zeros). Its point is put back where the
    total rates' sums then add up, if exactly one place does; otherwise that levy is left
    blank. A row with one cell missing (a population or a levy left empty) is read only if
    exactly one place for the blank makes its total rates add up and its taxes fit its
    valuation."""
    from itertools import product

    def row(values) -> dict:
        # "*": the city's part of another county (population on its main row)
        population = None if values[0] in (None, "*") else number(values[0])
        return {"population": population, "valuation": number(values[1]),
                **{c: number(v) for c, v in zip(columns, values[2:], strict=True)}}  # fmt: skip

    def choices(v, column) -> list:
        if v is None or not re.fullmatch(r"\d{3,}", v):
            return [v]
        if column not in CHECKED:
            return [None]
        return [p for i in range(1, len(v)) if float(p := v[:i] + "." + v[i:]) < 100]

    if len(numbers) == 2 + len(columns):
        gaps = [None]
    elif len(numbers) == 1 + len(columns):
        gaps = [0, *range(2, 2 + len(columns))]  # never the valuation
    else:
        return None
    fits = []
    for gap in gaps:
        values = numbers if gap is None else [*numbers[:gap], None, *numbers[gap:]]
        if any(v is not None and not re.fullmatch(r"[\d,]+(?:\.\d+)?|-|\*", v) for v in values):
            continue
        if values[1] in (None, "*", "-") or "." in values[1] or "*" in values[2:]:
            continue
        options = [choices(v, c) for v, c in zip(values[2:], columns, strict=True)]
        if gap is None and all(len(o) == 1 for o in options):
            return row([*values[:2], *(o[0] for o in options)])  # as read
        for levies in product(*options):
            r = row([*values[:2], *levies])
            if _rates_add_up(r) and _taxes_fit(r, taxes):
                fits.append(r | {"read_as": _read_as(columns, values, levies, gap)})
        if gap is None and len(fits) != 1:  # every cell there: leave the lost points blank
            levies = [o[0] if len(o) == 1 else None for o in options]
            return row([*values[:2], *levies]) | {"read_as": _read_as(columns, values, levies,
                                                                     gap)}  # fmt: skip
    return fits[0] if len(fits) == 1 else None


def _read_as(columns: list[str], values: list, levies, gap: int | None) -> str:
    """What was read differently from the line ("county_levy 2645 → 2.645"; "population
    blank"), for each row read with a blank or a lost point."""
    notes = [] if gap is None else [f"{(['population', 'valuation', *columns])[gap]} blank"]
    for c, v, w in zip(columns, values[2:], levies, strict=True):
        if v != w:
            notes.append(f"{c} {v} → {w if w is not None else 'left blank (point lost)'}")
    return "; ".join(notes)


# --- townships (two columns: each county's townships under its name) ----------------------

# a township that levied nothing has its levy and taxes blank ("CLEVELAND $39,293,251");
# a value is whole dollars, taxes have cents
TOWNSHIP_ROW = re.compile(
    r"^(?:\d{1,2} )?(?P<name>[A-Z][A-Z .'&-]*?)(?: (?P<value>\$[\d,]+))?(?: (?P<levy>\d*\.\d+))?"
    r"(?: (?P<tax>\$[\d,]+(?:\.\d\d)?))?$"
)


def townships(texts: list[str], file: str) -> tuple[list[dict], list[dict]]:
    """Each township's taxable value, mill levy and taxes, under its county; and the lines on
    those pages that weren't read. ("ALL UNORGANIZED": a county with no organized townships.)"""
    out, unread, county = [], [], None
    for n, text in enumerate(texts, start=1):
        if "TOWNSHIP VALUATION, LEVY, AND TAXES" not in " ".join(text.upper().split()):
            continue
        year = payable_year(text)
        for line in _lines(text):
            if (c := county_name(line)) is not None:
                county = c
            elif (m := TOWNSHIP_ROW.match(line)) and (
                m["value"] or m["tax"] or m["name"] == "ALL UNORGANIZED"  # 2018: no figures
            ):
                out.append({"payable_year": year, "county": county,
                            "township": " ".join(m["name"].split()),
                            "taxable_value": number(m["value"]), "levy": number(m["levy"]),
                            "taxes": number(m["tax"]), "file": file, "page": n})  # fmt: skip
            elif "$" in line:
                unread.append({"file": file, "page": n, "line": line})
    return out, unread


# --- county recaps -----------------------------------------------------------------------

COUNTY_HEADING = re.compile(r"RECAP INFORMATION\s*-\s*TAXES PAYABLE (\d{4}) COUNTY:?\s*(.+)$", re.I)
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
