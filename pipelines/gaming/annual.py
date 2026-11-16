"""Read the Commission on Gaming's calendar-year summary, 1989–2019 ("stats by denom", a PDF
with a text layer): each year's totals by game, and the year's totals.

Five pages of six or seven years each. A page has a "CALENDAR YEAR 1989 … 1994" header, then
for each game (Black Jack, House Banked Poker, Player Banked Poker, Craps, Roulette, Keno,
and slot machines by denomination, .01 to $25.00) its lines: No. Units, Drop (table games) or
Coins In (slots), Gross Rev, Ave Mnthly Hold/Unit (player-banked poker only), Ave Daily
Hold/Unit and Hold Percentage. Then the year's totals: Total # of Units, HANDLE, Table Game
Revenue, Slot Machine Revenue, Total Gross Revenue, Ave. Daily Hold/Unit and "Retails
Reporting Revenue" (sic).

Values are read by position: each word goes to the year whose column it sits under. A value
printed that isn't what the line holds (player-banked poker's daily hold for 2018 and 2019
is printed "100.00%"; 2000's .01 slots, "Combined with .05 slots in prior months"; "N/A";
"#DIV/0!") is empty, with what was printed kept in `note`.
"""

from __future__ import annotations

import re

MONEY = re.compile(r"^\(?-?\$-?[\d,]+(?:\.\d+)?\)?$")
PCT = re.compile(r"^-?[\d.]+%$")
INT = re.compile(r"^[\d,]+$")
MEASURES = {  # line label → (column, kind)
    "No. Units": ("units", "int"),
    "Drop": ("drop", "money"),
    "Coins In": ("coins_in", "money"),
    "Gross Rev": ("gross_revenue", "money"),
    "Ave Mnthly Hold/Unit": ("avg_monthly_hold_per_unit", "money"),
    "Ave Daily Hold/Unit": ("avg_daily_hold_per_unit", "money"),
    "Hold Percentage": ("hold_percent", "pct"),
}
TOTALS = {
    "Total # of Units": ("total_units", "int"),
    "HANDLE": ("handle", "money"),
    "Table Game Revenue": ("table_game_revenue", "money"),
    "Slot Machine Revenue": ("slot_machine_revenue", "money"),
    "Total Gross Revenue": ("total_gross_revenue", "money"),
    "Ave. Daily Hold/Unit": ("avg_daily_hold_per_unit", "money"),
    "Retails Reporting Revenue": ("retailers_reporting_revenue", "int"),
}
TABLE_GAMES = ("BLACK JACK", "HOUSE BANKED POKER", "PLAYER BANKED POKER", "CRAPS", "ROULETTE",
               "KENO")  # fmt: skip
SLOTS = re.compile(r"^\$?\d*\.\d\d SLOTS$")


class SummaryProblem(Exception):
    pass


def _value(text: str, kind: str):
    """A value of the kind the line holds, or None (with what was printed, for the note)."""
    t = text.strip()
    if kind == "int" and INT.match(t):
        return int(t.replace(",", "")), None
    if kind == "money" and MONEY.match(t):
        negative = t.startswith("(") or "-" in t
        v = float(t.strip("()").replace("$", "").replace(",", "").replace("-", ""))
        return (-v if negative else v), None
    if kind == "pct" and PCT.match(t):
        return float(t.rstrip("%")), None
    return None, t


def _lines(page) -> list[list[dict]]:
    words = sorted(page.extract_words(keep_blank_chars=False, use_text_flow=False),
                   key=lambda w: (round(w["top"]), w["x0"]))  # fmt: skip
    rows: list[list[dict]] = []
    for w in words:
        if rows and abs(rows[-1][0]["top"] - w["top"]) < 3:
            rows[-1].append(w)
        else:
            rows.append([w])
    return [sorted(r, key=lambda w: w["x0"]) for r in rows]


def read(content: bytes) -> tuple[list[dict], list[dict]]:
    """(rows by year and game, rows by year: the year's totals)."""
    import io

    import pdfplumber

    games: dict[tuple[int, str], dict] = {}
    years: dict[int, dict] = {}
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page in pdf.pages:
            lines = _lines(page)
            header = next((ln for ln in lines if ln[0]["text"] == "CALENDAR"), None)
            if not header:
                raise SummaryProblem(f"page {page.page_number}: no CALENDAR YEAR header")
            cols = [(int(w["text"]), (w["x0"] + w["x1"]) / 2) for w in header
                    if re.fullmatch(r"(19|20)\d\d", w["text"])]  # fmt: skip
            left = min(x for _, x in cols) - (cols[1][1] - cols[0][1]) / 2
            game = None
            for ln in lines:
                label = " ".join(w["text"] for w in ln if w["x1"] < left)
                values = [w for w in ln if w["x1"] >= left]
                if label in ("CALENDAR YEAR", "") or label.startswith("TOTALS"):
                    continue
                if not values and (label in TABLE_GAMES or SLOTS.match(label)):
                    game = label
                    continue
                spec = TOTALS.get(label) if label in TOTALS else MEASURES.get(label)
                if spec is None:
                    raise SummaryProblem(f"page {page.page_number}: unknown line {label!r}")
                if label in TOTALS and label != "Ave Daily Hold/Unit":
                    target = None  # a year's total
                elif game is None:
                    raise SummaryProblem(f"page {page.page_number}: {label!r} before a game")
                else:
                    target = game
                # each word under the year column it's nearest
                by_year: dict[int, list[str]] = {}
                for w in values:
                    mid = (w["x0"] + w["x1"]) / 2
                    year = min(cols, key=lambda c: abs(c[1] - mid))[0]
                    by_year.setdefault(year, []).append(w["text"])
                for year, _ in cols:
                    text = " ".join(by_year.get(year, []))
                    value, printed = _value(text, spec[1]) if text else (None, "")
                    if target is None:
                        row = years.setdefault(year, {"year": year})
                    else:
                        row = games.setdefault((year, target), {"year": year, "game": target})
                    row[spec[0]] = value
                    if printed and printed not in ("N/A", "#DIV/0!"):
                        row.setdefault("note", []).append(f"{label}: {printed}")
    for row in [*games.values(), *years.values()]:
        if "note" in row:
            row["note"] = "; ".join(row["note"])
    for (_, name), row in games.items():
        row["kind"] = "table game" if name in TABLE_GAMES else "slot machines"
    by_year = lambda r: r["year"]  # noqa: E731
    return sorted(games.values(), key=by_year), sorted(years.values(), key=by_year)


def checks(games: list[dict], years: list[dict]) -> list[str]:
    """What doesn't add up: each year's games' revenue against its totals, and each game's
    hold percentage against its revenue and drop (or coins in)."""
    out = []
    for y in years:
        rows = [g for g in games if g["year"] == y["year"]]
        tables = [g for g in rows if g["kind"] == "table game"]
        slots = [g for g in rows if g["kind"] == "slot machines"]
        # (HANDLE isn't the games' drop and coins in: player-banked poker's Drop is printed
        # as its revenue, the rake, and HANDLE has its wagers. Units are yearly averages,
        # rounded, so the games' come within a few of the total.)
        sums = {
            "table_game_revenue": sum(g["gross_revenue"] or 0 for g in tables),
            "slot_machine_revenue": sum(g["gross_revenue"] or 0 for g in slots),
        }
        sums["total_gross_revenue"] = sums["table_game_revenue"] + sums["slot_machine_revenue"]
        for key, s in sums.items():
            if y.get(key) is None or abs(s - y[key]) > 0.011 * len(rows):
                out.append(f"{y['year']}: {key} {y.get(key)}, the games add up to {s:,.2f}")
        for g in rows:
            base = g.get("drop") if g["kind"] == "table game" else g.get("coins_in")
            if g.get("hold_percent") and base:
                want = 100 * g["gross_revenue"] / base
                if abs(want - g["hold_percent"]) > 0.006:
                    out.append(f"{g['year']} {g['game']}: hold {g['hold_percent']}%, revenue "
                               f"÷ {'drop' if g['kind'] == 'table game' else 'coins in'} "
                               f"is {want:.2f}%")  # fmt: skip
    return out
