"""Read a month's municipal tax by city and SIC code workbook into rows.

The workbook is a printed report laid out in cells: for each city, a section for each tax
(General; Gross Receipts, the municipal gross receipts tax), with the city's taxable sales
and tax due by SIC division, major group and four-digit industry, and a total line for each;
then a recap of each section by division. Every row of the sheet names its city (the last
column).

Each line becomes a row with its `level`:
  industry          a four-digit SIC industry
  other             the industries of a major group too few to list ("Other")
  major group       a major group's total line
  division          a division's total line
  recap division    a division's line in the section's recap (divisions too few to list are
                    in "ZZ Other")
  tax total         the total for a city's tax
  city recap division  a division's tax due for all the city's taxes (the city's recap)
  city total        the city's tax due for all its taxes

"N/A" (fewer than three licenses) is kept as suppressed: the amount is empty and
`suppressed` is true. Totals include suppressed amounts.
"""

from __future__ import annotations

import io
import re

HEADER = ("Statistics for All Cities by SIC, Major Group and Division", "Totals", "Taxable",
          "Tax Due", "cont.", "City")  # fmt: skip
TAXES = ("General", "Gross Receipts")
DIVISION = re.compile(r"^DIVISION:\s+(?P<code>\S+)\s+(?P<name>.*)$")
MAJOR_GROUP = re.compile(r"^MAJOR GROUP:\s+(?:(?P<code>\d{2})\s+)?(?P<name>.*)$")
INDUSTRY = re.compile(r"^(?P<code>\d{4}|No SIC)\s+(?P<name>.*)$")
TOTAL = re.compile(r"^Total for (?:(?P<kind>MAJOR GROUP|DIVISION)\b\s*(?P<code>\S*)|(?P<what>.+))$")


class LayoutChanged(Exception):
    pass


def money(v) -> tuple[float | None, bool]:
    """A cell's amount and whether it's suppressed ("N/A")."""
    if v is None:
        return None, False
    if isinstance(v, int | float):
        return float(v), False
    t = str(v).strip()
    if t.upper() == "N/A":
        return None, True
    t = t.replace("$", "").replace(",", "")
    if t.startswith("(") and t.endswith(")"):
        t = "-" + t[1:-1]
    return (round(float(t), 2) if t else None), False


HEADING = re.compile(r"^(?P<entity>.+?)(?: - (?P<tax>General|Gross Receipts))?(?P<recap> Recap)?$")


def read(content: bytes, month: str, file: str) -> list[dict]:
    """The workbook's lines as rows. The city (or other taxing entity, such as "DENR - WINS")
    is taken from each section's heading, not the last column, which for some entities
    repeats a heading."""
    import openpyxl

    ws = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True).worksheets[0]
    rows = ws.iter_rows(values_only=True)
    header = tuple(next(rows))[:6]
    if header != HEADER:
        raise LayoutChanged(f"{file}: unknown header {header}")
    out = []
    entity = tax = division = major = None
    recap = False

    def add(n, level, taxable, tax_due, *, code=None, name=None):
        (t, ts), (d, ds) = money(taxable), money(tax_due)
        out.append({"month": month, "city": entity, "tax": tax, "level": level,
                    "division": division[0] if division else None,
                    "major_group": major[0] if major else None,
                    "code": code, "description": name, "taxable": t, "tax_due": d,
                    "suppressed": ts or ds, "file": file, "row": n})  # fmt: skip

    for n, r in enumerate(rows, start=2):
        a, b, c, d, e = [" ".join(str(x).split()) if x is not None else None
                         for x in (list(r) + [None] * 6)[:5]]  # fmt: skip
        if a is None and b is None and c is None:
            continue
        if (a, c) == ("Taxable", "Tax Due"):  # a column heading repeated (2024-03)
            continue
        if b and (m := TOTAL.match(b)):
            if m["kind"] == "MAJOR GROUP":
                add(n, "major group", c, d, code=major[0] if major else None,
                    name=major[1] if major else None)  # fmt: skip
            elif m["kind"] == "DIVISION":
                add(n, "division", c, d, code=division[0] if division else None,
                    name=division[1] if division else None)  # fmt: skip
            elif tax is not None and m["what"] == f"{entity} - {tax}":
                division = major = None
                add(n, "tax total", c, d)
            elif tax is None and m["what"] == entity:
                add(n, "city total", None, c)  # the city's recap: tax due, in the first column
            else:
                raise LayoutChanged(f"{file} row {n}: unknown total {b!r} in {entity} - {tax}")
            continue
        if recap and c is not None:  # (division code, None, division name, amounts…)
            division = major = None
            if tax is None:  # the city's recap: tax due only, in the first column
                add(n, "city recap division", None, d, code=a, name=c)
            else:
                add(n, "recap division", d, e, code=a, name=c)
            continue
        if not a:
            continue
        if m := DIVISION.match(a):
            division, major = (m["code"], m["name"]), None
        elif m := MAJOR_GROUP.match(a):
            major = (m["code"] or None, m["name"])
        elif a == "Other" and not recap:
            add(n, "other", c, d, name="Other")
        elif (m := INDUSTRY.match(a)) and not recap:
            add(n, "industry", c, d, code=m["code"], name=m["name"])
        elif b is None and c is None and d is None and (h := HEADING.match(a)):
            entity, tax, recap = h["entity"], h["tax"], bool(h["recap"])
            division = major = None
        else:
            raise LayoutChanged(f"{file} row {n}: unknown line {a!r}")
    return out


# --- PDF reports ---------------------------------------------------------------------------


def pdf_lines(content: bytes) -> list[tuple[int, str]]:
    """(page number, line) for each line of a PDF's text, spaces closed up."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(content)
    try:
        out = []
        for i in range(len(doc)):
            text = doc[i].get_textpage().get_text_bounded().replace("\r", "")
            out += [(i + 1, " ".join(x.split())) for x in text.splitlines() if x.strip()]
        return out
    finally:
        doc.close()


AMOUNT = r"-?[\d,]+\.\d\d"
RETURNS_FILED = re.compile(r"Returns Filed:? (?:in )?([A-Z][a-z]+ \d{4})")
TOURISM_LINE = re.compile(rf"^(?:(?P<code>\d{{3,4}}) (?P<name>.+?)|(?P<other>Other)) "
                          rf"(?P<taxable>{AMOUNT}|N/A) (?P<tax_due>{AMOUNT}|N/A)$")  # fmt: skip
TOURISM_STATE = re.compile(rf"^Total for State (?P<taxable>{AMOUNT}) (?P<tax_due>{AMOUNT})$")
COUNTY = re.compile(r"^(?P<county>[A-Z][A-Za-z .]+) County$")


def _pdf_money(v: str) -> tuple[float | None, bool]:
    return money(v)


def tourism(content: bytes, month: str, file: str) -> tuple[list[dict], list[dict]]:
    """The tourism tax report: each county's taxable sales and tax due by industry, "Other"
    (suppressed where N/A), and the state's total. Also the lines with amounts that weren't
    read."""
    out, unread, county = [], [], None
    for page, line in pdf_lines(content):
        if m := RETURNS_FILED.search(line):
            filed = m[1]
            if _month(filed) != month:
                raise LayoutChanged(f"{file}: returns filed {filed}, not {month}")
            continue
        if m := COUNTY.match(line):
            county = m["county"]
        elif m := TOURISM_STATE.match(line):
            out.append({"month": month, "county": None, "level": "state total", "code": None,
                        "description": None, "taxable": money(m["taxable"])[0],
                        "tax_due": money(m["tax_due"])[0], "suppressed": False,
                        "file": file, "page": page})  # fmt: skip
        elif (m := TOURISM_LINE.match(line)) and county:
            (t, ts), (d, ds) = money(m["taxable"]), money(m["tax_due"])
            out.append({"month": month, "county": county,
                        "level": "other" if m["other"] else "industry", "code": m["code"],
                        "description": m["name"] or "Other", "taxable": t, "tax_due": d,
                        "suppressed": ts or ds, "file": file, "page": page})  # fmt: skip
        elif re.search(AMOUNT + r"$", line):
            unread.append({"file": file, "page": page, "line": line})
    return out, unread


# a city, its tax due, and (unless it had none) the year before's and the change; a
# negative amount can run into the next ("-5,499.10-398.68")
CITY_DUE = re.compile(rf"(?P<city>[A-Z][A-Za-z.' -]*?) (?P<now>{AMOUNT})(?: ?(?P<before>{AMOUNT})"
                      rf" ?(?P<change>-?[\d,]+\.\d+|N/A)?)?(?= [A-Z]|$)")  # fmt: skip
CITY_DUE_TITLE = re.compile(r"Municipal Tax Due for Returns Filed in ([A-Z][a-z]+) ?(\d{4}) and "
                            r"(\d{4})")  # fmt: skip


def _month(text: str) -> str:
    from datetime import datetime

    return datetime.strptime(text, "%B %Y").strftime("%Y-%m")


def city_tax_due(content: bytes, month: str, file: str) -> tuple[list[dict], list[dict]]:
    """Each city's municipal tax due (general and gross receipts) for the month, and for the
    same month a year before as this report gives it, with the percent change."""
    out, unread, titled, listing = [], [], False, None
    for page, line in pdf_lines(content):
        if m := CITY_DUE_TITLE.search(line):
            if _month(f"{m[1]} {m[2]}") != month:
                raise LayoutChanged(f"{file}: returns filed {m[1]} {m[2]}, not {month}")
            titled = True
            continue
        if line.startswith("CITY "):
            # the ten largest cities first ("% Change"), then every city alphabetically
            listing = "largest" if "% Change" in line else "all"
            continue
        if not titled or line.startswith(("Figures compiled", "The dollar amounts",
                                          "These figures")):  # fmt: skip
            continue
        found = list(CITY_DUE.finditer(line))
        if not found or "".join(x[0] for x in found).replace(" ", "") != line.replace(" ", ""):
            unread.append({"file": file, "page": page, "line": line})
            continue
        for m in found:
            out.append({"month": month, "city": m["city"].strip(), "listing": listing,
                        "tax_due": money(m["now"])[0],
                        "tax_due_year_before": money(m["before"])[0] if m["before"] else None,
                        "percent_change": (None if m["change"] in (None, "N/A")
                                           else float(m["change"].replace(",", ""))),
                        "file": file, "page": page})  # fmt: skip
    if not titled:
        raise LayoutChanged(f"{file}: no 'Municipal Tax Due for Returns Filed in' title")
    return out, unread
