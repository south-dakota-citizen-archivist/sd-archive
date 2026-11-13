"""Read a month's South Dakota Gaming Statistics Monthly Summary (Commission on Gaming, PDF).

Page 1: slot machines by denomination and table games by game (units, handle, statistical win,
average payout), sports wagering's totals (casinos reporting, handle, win, payout), the handle
comparison (the month and the calendar year to date, with the change from a year before), the
tax calculation (from total statistical win to the taxable adjusted gross revenue and the 9%
tax) and the tax's distribution. Page 2: sports wagering by sport.

Lines are read from the PDF's text in reading order (pdfplumber), and each section by its
heading. Amounts are kept as printed: a payout percentage of "N/A" or "#DIV/0!" is None, and
"(1,234.56)" is negative.
"""

from __future__ import annotations

import io
import re
from datetime import datetime

MONEY = r"\(?-?\$-?[\d,]+(?:\.\d+)?\)?"
PCT = r"-?[\d.,]+%|N/A|#DIV/0!"
TITLE = re.compile(r"^(January|February|March|April|May|June|July|August|September|October|"
                   r"November|December) (\d{4})$")  # fmt: skip
UNITS_ROW = re.compile(rf"^(?P<name>.+?) (?P<units>\d+) (?P<handle>{MONEY}) (?P<win>{MONEY}) "
                       rf"(?P<payout>{PCT})$")  # fmt: skip
SPORT_ROW = re.compile(rf"^(?P<name>.+?) (?P<handle>{MONEY}) (?P<win>{MONEY}) (?P<payout>{PCT})$")
COMPARISON_ROW = re.compile(rf"^(?P<name>Slot Machines|Table Games|Sports Wagering|Total Handle) "
                            rf"(?P<month>{MONEY}) (?P<month_change>{PCT}) (?P<ytd>{MONEY}) "
                            rf"(?P<ytd_change>{PCT})$")  # fmt: skip
# a label (which can have parentheses: "Less: City Slots (Net of Promo)") and its amount
PAIR = re.compile(rf"(?P<label>[A-Za-z0-9%][^$]*?):? (?P<amount>{MONEY})(?= |$)")
SECTIONS = ("Slot Machines", "Table Games", "Sports Wagering", "Handle Comparison",
            "Tax Calculation")  # fmt: skip
# the tax calculation's and distribution's lines, by a word or two of their labels
TAX_LINES = {
    "total statistical win": "total_statistical_win",
    "non cashable promo play": "less_non_cashable_promo_play",
    "non cashable promo wins": "plus_non_cashable_promo_wins",
    "wide area progressive": "less_wide_area_progressive",
    "city slots": "less_city_slots",
    "expired vouchers": "plus_expired_vouchers",
    "federal excise tax": "less_federal_excise_tax",
    "amended returns": "adjustments_amended_returns_and_audits",
    "audit adjustments": "adjustments_amended_returns_and_audits",
    "taxable adjusted gross revenue": "taxable_adjusted_gross_revenue",
    "tax due": "tax_due",
    "total 9% tax collected": "tax_collected",
    "1% to state general fund": "to_state_general_fund",
    "8% to be distributed": "to_be_distributed",
    "40% allocated to tourism": "to_tourism",
    "10% allocated to lawrence county": "to_lawrence_county",
    "50% commission fund": "to_commission_fund",
}  # fmt: skip


class ReportProblem(Exception):
    pass


def money(text: str) -> float:
    t = text.strip()
    negative = t.startswith("(") or "-" in t
    value = float(t.strip("()").replace("$", "").replace(",", "").replace("-", ""))
    return -value if negative else value


def percent(text: str) -> float | None:
    t = text.strip()
    if t in ("N/A", "#DIV/0!"):
        return None
    return float(t.rstrip("%").replace(",", ""))


def lines(content: bytes, ocr: str | None = None) -> list[list[str]]:
    """Each page's lines: from the text layer, or (ocr: an sddocs engine) read by OCR, for a
    report that's a scan."""
    import pdfplumber

    if ocr:
        import tempfile

        from sddocs import ocr as engines

        with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
            f.write(content)
            f.flush()
            with pdfplumber.open(f.name) as pdf:
                n = len(pdf.pages)
            return [[_ocr_words(" ".join(x.split())) for x in engines.as_text(
                engines.page_pieces(f.name, i, ocr), spacing=" ").splitlines() if x.strip()]
                for i in range(n)]  # fmt: skip
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        return [[" ".join(x.split()) for x in (p.extract_text(x_tolerance=1.5, y_tolerance=3)
                                                or "").splitlines() if x.strip()]
                for p in pdf.pages]  # fmt: skip


def _ocr_words(line: str) -> str:
    """Words OCR misreads in these reports ("Totais"); the figures aren't touched, and the
    build's checks still have to pass for a scan to be used."""
    return re.sub(r"^Totai[s5]\b", "Totals", line)


def is_scan(content: bytes) -> bool:
    import pdfplumber

    with pdfplumber.open(io.BytesIO(content)) as pdf:
        return all(len(p.chars) < 30 for p in pdf.pages)


def _rows(regex, block: list[str], kind: str) -> tuple[list[dict], dict | None]:
    out, total = [], None
    for line in block:
        m = regex.match(line)
        if not m:
            continue
        row = {
            "name": m["name"].strip(),
            "handle": money(m["handle"]),
            "statistical_win": money(m["win"]),
            "payout_percent": percent(m["payout"]),
        }
        if "units" in m.groupdict():
            row["units"] = int(m["units"])
        if row["name"] == "Totals":
            total = row
        else:
            out.append(row)
    if total is None:
        raise ReportProblem(f"no Totals line for {kind}")
    return out, total


def read(content: bytes, ocr: str | None = None) -> dict:
    pages = lines(content, ocr)
    if len(pages) < 1:
        raise ReportProblem("no pages")
    first = pages[0]
    month = None
    for line in first[:4]:
        if m := TITLE.match(line.replace("##########", "").strip()):
            month = datetime.strptime(f"{m[1]} {m[2]}", "%B %Y").strftime("%Y-%m")
    if month is None:
        raise ReportProblem("no month in the title")
    # split page 1 into sections by heading (the first occurrence of each)
    at = {}
    for i, line in enumerate(first):
        for s in SECTIONS:
            if (line == s or line.startswith(s + " ") and s == "Tax Calculation") and s not in at:
                at[s] = i
    missing = [s for s in SECTIONS if s not in at]
    if missing:
        raise ReportProblem(f"no {', '.join(missing)} section")
    bounds = sorted(at.values()) + [len(first)]
    block = {s: first[at[s] + 1 : next(b for b in bounds if b > at[s])] for s in SECTIONS}

    slots, slot_total = _rows(UNITS_ROW, block["Slot Machines"], "slot machines")
    games, games_total = _rows(UNITS_ROW, block["Table Games"], "table games")
    _, sports_total = _rows(UNITS_ROW, block["Sports Wagering"], "sports wagering")
    sports_total["casinos_reporting"] = sports_total.pop("units")
    comparison = {}
    for line in block["Handle Comparison"]:
        if m := COMPARISON_ROW.match(line):
            comparison[m["name"]] = {"handle": money(m["month"]),
                                     "change_from_a_year_before": percent(m["month_change"]),
                                     "handle_year_to_date": money(m["ytd"]),
                                     "year_to_date_change": percent(m["ytd_change"])}  # fmt: skip
    if len(comparison) != 4:
        raise ReportProblem(f"handle comparison: {sorted(comparison)}")
    tax, pending = {}, None
    for line in [first[at["Tax Calculation"]], *block["Tax Calculation"]]:
        if pending and re.fullmatch(MONEY, line):
            # a label printed over two lines, its amount on a line of its own between them
            tax.setdefault(pending, money(line))
            pending = None
            continue
        found = False
        for m in PAIR.finditer(line):
            label = m["label"].lower()
            key = next((v for k, v in TAX_LINES.items() if k in label), None)
            if key and key not in tax:
                tax[key] = money(m["amount"])
                found = True
        if not found:
            pending = next((v for k, v in TAX_LINES.items() if k in line.lower()), None)
    absent = [v for v in ("total_statistical_win", "taxable_adjusted_gross_revenue", "tax_due",
                          "tax_collected") if v not in tax]  # fmt: skip
    if absent:
        raise ReportProblem(f"tax lines not found: {absent}")

    # the sports detail is on page 2 (a scan of one page has none)
    sports, sports_detail_total = ((_rows(SPORT_ROW, pages[1], "sports wagering by sport"))
                                   if len(pages) > 1 else ([], None))  # fmt: skip
    return {"month": month, "slots": slots, "slots_total": slot_total, "table_games": games,
            "table_games_total": games_total, "sports_wagering_total": sports_total,
            "sports": sports, "sports_detail_total": sports_detail_total,
            "handle_comparison": comparison, "tax": tax}  # fmt: skip
