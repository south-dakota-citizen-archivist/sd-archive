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
