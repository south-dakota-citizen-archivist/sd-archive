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
import itertools
import re
from datetime import datetime

MONEY = r"\(?-?\$-?[\d,]+(?:\.\d+)?\)?"
PCT = r"-?\d{1,3}(?:,\d{3})+(?:\.\d+)?%|-?\d+(?:\.\d+)?%|N/A|#DIV/0!"  # never "11,29%"
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
    "expired vouchers (sports wagering)": "plus_expired_vouchers_sports_wagering",
    "expired vouchers": "plus_expired_vouchers",
    "federal excise tax": "less_federal_excise_tax",
    "amended returns": "adjustments_amended_returns_and_audits",  # 2018: "/Court Settlement"
    "audit adjustments": "adjustments_amended_returns_and_audits",
    "taxable adjusted gross revenue": "taxable_adjusted_gross_revenue",
    "tax due": "tax_due",
    "total 9% tax collected": "tax_collected",
    "1% to state general fund": "to_state_general_fund",
    "8% to be distribut": "to_be_distributed",  # 2018–19 misspell "Distributed"
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
            return [ocr_lines(engines.page_pieces(f.name, i, ocr)) for i in range(n)]
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        return [[" ".join(x.split()) for x in (p.extract_text(x_tolerance=1.5, y_tolerance=3)
                                                or "").splitlines() if x.strip()]
                for p in pdf.pages]  # fmt: skip


# rows as named in the reports; an OCR misreading of a name ("Caeps") is read as the closest
SLOT_NAMES = ("25 Dollars", "5 Dollars", "1 Dollar", "50 Cents", "25 Cents", "10 Cents",
              "City Slot", "5 Cents", "1 Cents", "Totals")  # fmt: skip
GAME_NAMES = ("Black Jack", "House Banked Poker", "Player Banked Poker", "Craps", "Roulette",
              "Keno", "Totals")  # fmt: skip
ROW_NAMES = "|".join(re.escape(n) for n in sorted({*SLOT_NAMES, *GAME_NAMES}, key=len,
                                                   reverse=True))  # fmt: skip


def ocr_lines(pieces: list[dict], tolerance: float = 5) -> list[str]:
    """A page's OCR pieces as lines: pieces whose middles are within `tolerance` points of
    the line's first are one line."""
    from sddocs import ocr as engines

    out = []
    for x in engines.as_text(pieces, tolerance=tolerance, spacing=" ").splitlines():
        out += _ocr_words(" ".join(x.split()))
    return [x for x in out if x]


def _ocr_words(line: str) -> list[str]:
    """What OCR does to these reports' lines that isn't a figure: characters no report prints
    (Vision's "ął" after a figure), "Totais", and a Totals line run onto the row above. The
    figures aren't touched, and the build's checks still have to pass for a scan to be used."""
    line = " ".join(re.sub(r"[^\x20-\x7e]", "", line).split())
    line = re.sub(r"^Totai[s5]\b", "Totals", line)
    # figures run together: "1988$99,245,467.26", "$9,526,357.2890.40%", "$7,126,372.00$1,3…"
    line = re.sub(r"(?<=[\d)%])(?=\$|\(\$)", " ", line)
    line = re.sub(r"(?<=\.\d\d)(?=\d+\.\d+%)", " ", line)
    line = re.sub(r"(?<= )- (?=\d[\d.]*%)", "-", line)  # "- 11.29%"
    # a row run onto the one before it ("… 0% 1 Cents 0 $0.00 …", "… 90.59% Totals 2552 …")
    return [x.strip() for x in re.split(rf"(?<=%|A) (?=(?:{ROW_NAMES}) \d)", line)]


def _name(name: str, known: tuple[str, ...] | None) -> str:
    from difflib import get_close_matches

    if not known or name in known:
        return name
    close = get_close_matches(name, known, n=1, cutoff=0.75)
    # the digits of a denomination have to match: "5 Cents" is never "25 Cents"
    if close and re.sub(r"\D", "", close[0]) == re.sub(r"\D", "", name):
        return close[0]
    return name


def is_scan(content: bytes) -> bool:
    import pdfplumber

    with pdfplumber.open(io.BytesIO(content)) as pdf:
        return all(len(p.chars) < 30 for p in pdf.pages)


def _joined(regex, block: list[str]) -> list[str]:
    """A scan's row split over two lines ("Totals 92" / "$7,126,372.00 $1,376,396.02 79.37%")
    as one, where neither line is a row and the two together are."""
    out, i = [], 0
    while i < len(block):
        line = block[i]
        if not regex.match(line) and i + 1 < len(block) and not regex.match(block[i + 1]):
            nxt = block[i + 1]
            for joined in (f"{line} {nxt}",
                           # units on a line of their own above the row ("0" / "Totals $0.00…")
                           re.sub(r"^(\D+?) (?=\(?\$)", rf"\1 {line} ", nxt, count=1)
                           if re.fullmatch(r"\d+", line) else ""):  # fmt: skip
                if joined and regex.match(joined):
                    out.append(joined)
                    i += 2
                    break
            else:
                out.append(line)
                i += 1
            continue
        out.append(line)
        i += 1
    return out


def _rows(regex, block: list[str], kind: str, known=None) -> tuple[list[dict], dict | None]:
    out, total = [], None
    for line in _joined(regex, block):
        m = regex.match(line)
        if not m:
            continue
        row = {
            "name": _name(m["name"].strip(), known),
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


COMPARISON_MONEY = re.compile(rf"^({MONEY}) ({MONEY})$")
COMPARISON_PCT = re.compile(rf"^(Slot Machines|Table Games|Sports Wagering|Total Handle) "
                            rf"({PCT}) ({PCT})$")  # fmt: skip


def _comparison_lines(block: list[str]) -> list[str]:
    """A scan can print a handle comparison row's two amounts a little above (or below) its
    name and percentages, as a line of their own: put them back in their columns."""
    out, i = [], 0
    while i < len(block):
        pair = block[i : i + 2]
        if len(pair) == 2:
            money_first = COMPARISON_MONEY.match(pair[0]), COMPARISON_PCT.match(pair[1])
            pct_first = COMPARISON_PCT.match(pair[0]), COMPARISON_MONEY.match(pair[1])
            # the year to date alone above the rest ("$15,715,980.60" / "Table Games $0.00
            # -100% -30.80%")
            ytd = re.fullmatch(MONEY, pair[0]) and re.match(
                rf"^(.+?) ({MONEY}) ({PCT}) ({PCT})$", pair[1])  # fmt: skip
            for amounts, pcts in (money_first, pct_first[::-1]):
                if amounts and pcts:
                    out.append(f"{pcts[1]} {amounts[1]} {pcts[2]} {amounts[2]} {pcts[3]}")
                    i += 2
                    break
            else:
                if ytd:
                    out.append(f"{ytd[1]} {ytd[2]} {ytd[3]} {pair[0]} {ytd[4]}")
                    i += 2
                else:
                    out.append(block[i])
                    i += 1
            continue
        out.append(block[i])
        i += 1
    return out


def _comparison_in_order(block: list[str]) -> dict:
    """A scan's handle comparison read in order, not by line: the rows are always Slot
    Machines, Table Games, (Sports Wagering,) Total Handle, each with two amounts (the month,
    the year to date) and two percentages, however the scan scatters them over lines and
    wherever their names land. Empty unless the amounts and percentages come out even."""
    amounts, pcts = [], []
    for line in block:
        for m in re.finditer(rf"(?P<money>{MONEY})|(?P<pct>{PCT})", line):
            (amounts if m["money"] else pcts).append(m[0])
    rows = len(amounts) // 2
    if rows not in (3, 4) or len(amounts) != 2 * rows or len(pcts) != 2 * rows:
        return {}
    names = ["Slot Machines", "Table Games", *(["Sports Wagering"] if rows == 4 else []),
             "Total Handle"]  # fmt: skip
    return {name: {"handle": money(amounts[2 * i]),
                   "change_from_a_year_before": percent(pcts[2 * i]),
                   "handle_year_to_date": money(amounts[2 * i + 1]),
                   "year_to_date_change": percent(pcts[2 * i + 1])}
            for i, name in enumerate(names)}  # fmt: skip


def _heading(line: str, section: str) -> bool:
    if line == section:
        return True
    if section == "Tax Calculation":  # "Tax Calculation Tax Distribution"
        return line.startswith(section + " ")
    # a scan can run the heading into its column headers ("Number of Casinos Sports Wagering")
    # or cut it off ("ng" / "Sports Wager")
    return section == "Sports Wagering" and "Sports Wager" in line and "$" not in line


def _tax_key(label: str) -> str | None:
    flat = label.lower().replace(" ", "")  # OCR drops spaces: "Total 9%Tax Collected"
    return next((v for k, v in TAX_LINES.items() if k.replace(" ", "") in flat), None)


def _tax(block: list[str]) -> dict:
    tax, pending, loose = {}, None, None
    for line in block:
        if re.fullmatch(MONEY, line):
            # a label printed over two lines, its amount on a line of its own between them;
            # or (a scan's page break) the amount on the line before its label
            if pending:
                tax.setdefault(pending, money(line))
                pending = None
            else:
                loose = money(line)
            continue
        end = 0
        for m in PAIR.finditer(line):
            key = _tax_key(m["label"])
            if key is None and pending and end == 0:
                # the end of a label begun on the line before ("Less: Wide Area Progressive"
                # / "Deduction ($11,472.08)")
                key = pending
            pending = None if end == 0 else pending
            if key and key not in tax:
                tax[key] = money(m["amount"])
            end = m.end()
        rest = line[end:]
        key = _tax_key(rest) if rest.strip() else None
        if key and key not in tax:
            pending = key
            if loose is not None:
                tax[pending], pending, loose = loose, None, None
    return tax


MONTHLY_TITLE = "South Dakota Gaming Statistics Monthly Summary"


def read(content: bytes, ocr: str | None = None) -> dict:
    return read_lines(lines(content, ocr))


def read_lines(pages: list[list[str]], month: str | None = None, strict: bool = True) -> dict:
    """A report from its pages' lines. `month` (YYYY-MM): for a scan whose title can't be
    read. Not strict: a section that can't be read is left out (None, or empty) instead of
    failing the report, for a scan's readings to be combined."""
    if len(pages) < 1:
        raise ReportProblem("no pages")
    first = pages[0]
    for line in first[:4]:
        if month is None and (m := TITLE.match(line.replace("##########", "").strip())):
            month = datetime.strptime(f"{m[1]} {m[2]}", "%B %Y").strftime("%Y-%m")
    if month is None:
        raise ReportProblem("no month in the title")
    # split page 1 into sections by heading (the first occurrence of each)
    at = {}
    for i, line in enumerate(first):
        for s in SECTIONS:
            if s not in at and _heading(line, s):
                at[s] = i
    # sports wagering began in September 2021; the reports before have no section for it
    missing = [s for s in SECTIONS if s not in at and s != "Sports Wagering"]
    if missing and strict:
        raise ReportProblem(f"no {', '.join(missing)} section")
    bounds = sorted(at.values()) + [len(first)]
    block = {s: first[at[s] + 1 : next(b for b in bounds if b > at[s])] for s in at}

    def section(regex, name, kind, known=None):
        try:
            return _rows(regex, block.get(name, []), kind, known)
        except ReportProblem:
            if strict:
                raise
            return [], None

    slots, slot_total = section(UNITS_ROW, "Slot Machines", "slot machines", SLOT_NAMES)
    games, games_total = section(UNITS_ROW, "Table Games", "table games", GAME_NAMES)
    sports_total = None
    if "Sports Wagering" in block:
        _, sports_total = section(UNITS_ROW, "Sports Wagering", "sports wagering")
        if sports_total:
            sports_total["casinos_reporting"] = sports_total.pop("units")
    comparison = {}
    for line in _comparison_lines(block.get("Handle Comparison", [])):
        if m := COMPARISON_ROW.match(line):
            comparison[m["name"]] = {"handle": money(m["month"]),
                                     "change_from_a_year_before": percent(m["month_change"]),
                                     "handle_year_to_date": money(m["ytd"]),
                                     "year_to_date_change": percent(m["ytd_change"])}  # fmt: skip
    if not strict and len(comparison) < (4 if sports_total else 3):
        comparison = _comparison_in_order(block.get("Handle Comparison", [])) or comparison
    if strict and (len(comparison) != (4 if sports_total else 3)
                   or ("Sports Wagering" in comparison and not sports_total)):  # fmt: skip
        raise ReportProblem(f"handle comparison: {sorted(comparison)}")
    # a scan's tax calculation can run onto page 2, above the sports detail's title
    spill = []
    if len(pages) > 1:
        spill = list(itertools.takewhile(
            lambda x: x != MONTHLY_TITLE and not x.startswith("Sporting Event")
            and not SPORT_ROW.match(x), pages[1]))  # fmt: skip
    tax_block = [first[at["Tax Calculation"]], *block["Tax Calculation"], *spill] \
        if "Tax Calculation" in at else []  # fmt: skip
    tax = _tax(tax_block)
    absent = [v for v in ("total_statistical_win", "taxable_adjusted_gross_revenue", "tax_due",
                          "tax_collected") if v not in tax]  # fmt: skip
    if absent and strict:
        raise ReportProblem(f"tax lines not found: {absent}")

    # the sports detail is on page 2 (a scan of one page has none)
    sports, sports_detail_total = [], None
    if len(pages) > 1:
        try:
            sports, sports_detail_total = _rows(SPORT_ROW, pages[1][len(spill):],
                                                "sports wagering by sport")  # fmt: skip
        except ReportProblem:
            if strict:
                raise
    return {"month": month, "slots": slots, "slots_total": slot_total, "table_games": games,
            "table_games_total": games_total, "sports_wagering_total": sports_total,
            "sports": sports, "sports_detail_total": sports_detail_total,
            "handle_comparison": comparison, "tax": tax}  # fmt: skip
