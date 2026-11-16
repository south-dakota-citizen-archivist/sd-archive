"""Build tidy tables of South Dakota's licensed gaming (Deadwood) by month from the
Commission on Gaming's monthly summaries.

    uv run python pipelines/gaming/build.py

Output (in .cache/gaming/build/, not committed):
  slots.parquet / .csv        month × slot denomination: units, handle, statistical win, payout
  table_games.parquet / .csv  month × game (blackjack, poker, craps, roulette): the same
  sports.parquet / .csv       month × sport: handle, statistical win, payout (sports wagering)
  monthly.parquet / .csv      one row per month: the totals for slots, table games and sports
                              wagering, total handle and the calendar year to date, the tax
                              calculation (total statistical win → taxable adjusted gross
                              revenue → the 9% tax) and the tax's distribution
  checks.json                 what doesn't add up (also printed)

A report that's a scan (no text) is read by OCR (rapidocr) and used only if every check below
passes for it; otherwise it's left out and listed. Each row names its report's file and URL,
and how it was read.

Checks, for every month:
  - the denominations and games add up to their Totals lines (units, handle and win), and the
    sports add up to sports wagering's totals
  - each payout percentage is (handle - win) / handle (a Totals line's, over the rows that
    have a payout: player-banked poker, where the house takes a rake, has none)
  - the handle comparison's current figures are the sections' totals, and the year to date is
    the previous month's plus this month's
  - total statistical win is slots + table games + sports wagering; the adjustments add up to
    the taxable adjusted gross revenue; the tax due is 9% of it; the distribution's shares
    are 1% and 8% of it, and 40%, 10% and 50% of the 8%
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

import annual
import pandas as pd
import parse  # pipelines/gaming/parse.py (run as a script, so this folder is on the path)
import scans

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
RAW = REPO / ".cache" / "gaming" / "raw"
BUILD = REPO / ".cache" / "gaming" / "build"
CENT = 0.011
ADJUSTMENTS = ["less_non_cashable_promo_play", "plus_non_cashable_promo_wins",
               "less_wide_area_progressive", "less_city_slots", "plus_expired_vouchers",
               "less_federal_excise_tax", "adjustments_amended_returns_and_audits",
               "plus_expired_vouchers_sports_wagering"]  # fmt: skip
SECTIONS = {"Slot Machines": "slots_total", "Table Games": "table_games_total",
            "Sports Wagering": "sports_wagering_total"}  # fmt: skip


def report_checks(r: dict) -> list[str]:
    """What doesn't add up within one month's report."""
    out = []

    def near(a, b, tol=CENT):
        return a is not None and b is not None and abs(a - b) <= tol

    for rows, total, label in (
        (r["slots"], r["slots_total"], "slot machines"),
        (r["table_games"], r["table_games_total"], "table games"),
    ):
        for key in ("units", "handle", "statistical_win"):
            s = sum(x[key] for x in rows)
            if not near(s, total[key], CENT * len(rows)):
                out.append(f"{label}: {key} adds up to {s:,.2f}, Totals line {total[key]:,.2f}")
    if r["sports"]:
        for key in ("handle", "statistical_win"):
            s = sum(x[key] for x in r["sports"])
            if not near(s, r["sports_wagering_total"][key], CENT * len(r["sports"])):
                out.append(f"sports: {key} adds up to {s:,.2f}, sports wagering total "
                           f"{r['sports_wagering_total'][key]:,.2f}")  # fmt: skip

    def payout(rows):  # over the rows that have a payout (player-banked poker is a rake: N/A)
        paid = [x for x in rows if x["payout_percent"] is not None]
        handle = sum(x["handle"] for x in paid)
        return 100 * (handle - sum(x["statistical_win"] for x in paid)) / handle if handle else None

    present = {n: k for n, k in SECTIONS.items() if r[k]}  # no sports wagering before 2021-09
    for rows, total, label in ((r["slots"], r["slots_total"], "slots"),
                               (r["table_games"], r["table_games_total"], "table games"),
                               ([], r["sports_wagering_total"], "sports wagering")):  # fmt: skip
        for x in [*rows, total] if total else []:
            if x["payout_percent"] is None or not x["handle"]:
                continue
            want = (payout(rows) if x is total and rows
                    else 100 * (x["handle"] - x["statistical_win"]) / x["handle"])  # fmt: skip
            if abs(want - x["payout_percent"]) > 0.06:
                out.append(f"{label} {x['name']}: payout {x['payout_percent']}%, "
                           f"(handle - win) / handle is {want:.2f}%")  # fmt: skip
    hc = r["handle_comparison"]
    for name, key in present.items():
        if not near(hc[name]["handle"], r[key]["handle"]):
            out.append(f"handle comparison: {name} {hc[name]['handle']:,.2f}, section total "
                       f"{r[key]['handle']:,.2f}")  # fmt: skip
    total = sum(hc[n]["handle"] for n in present)
    if not near(total, hc["Total Handle"]["handle"], 0.05):
        out.append(f"total handle {hc['Total Handle']['handle']:,.2f}, sections {total:,.2f}")
    t = r["tax"]
    win = sum(r[k]["statistical_win"] for k in present.values())
    if not near(win, t["total_statistical_win"], 0.05):
        out.append(f"total statistical win {t['total_statistical_win']:,.2f}, sections "
                   f"{win:,.2f}")  # fmt: skip
    agr = t["total_statistical_win"] + sum(t.get(k, 0) for k in ADJUSTMENTS)
    if not near(agr, t["taxable_adjusted_gross_revenue"], 0.05):
        out.append(f"taxable AGR {t['taxable_adjusted_gross_revenue']:,.2f}, win and "
                   f"adjustments {agr:,.2f}")  # fmt: skip
    if not near(0.09 * t["taxable_adjusted_gross_revenue"], t["tax_due"], 0.02):
        out.append(f"tax due {t['tax_due']:,.2f} isn't 9% of the taxable AGR")
    if not near(t["tax_due"], t["tax_collected"]):
        out.append(f"tax due {t['tax_due']:,.2f}, collected {t['tax_collected']:,.2f}")
    agr_ = t["taxable_adjusted_gross_revenue"]
    shares = {"to_state_general_fund": 0.01 * agr_, "to_be_distributed": 0.08 * agr_,
              "to_tourism": 0.4 * 0.08 * agr_, "to_lawrence_county": 0.1 * 0.08 * agr_,
              "to_commission_fund": 0.5 * 0.08 * agr_}  # fmt: skip
    for key, want in shares.items():
        if key in t and not near(t[key], want, 0.02):
            out.append(f"{key} {t[key]:,.2f}, its share is {want:,.2f}")
    return out


def read_month(
    content: bytes,
    path: Path | None = None,
    month: str | None = None,
    pages: list[int] | None = None,
) -> tuple[dict | None, str, list[str]]:
    """A report, how it was read, and what doesn't add up (a scan that fails a check isn't
    used: None). A scan is read from `path` (its `pages` of it: a month in a yearly file)."""
    if not parse.is_scan(content):
        r = parse.read(content)
        return r, "text", report_checks(r)
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        if path is None:
            path = Path(tmp) / "report.pdf"
            path.write_bytes(content)
        if pages is None:
            import pdfplumber

            with pdfplumber.open(path) as pdf:
                pages = list(range(len(pdf.pages)))
        return scans.read_scan(path, pages, month)


def month_of(text: str) -> str | None:
    """ "August 2023" → "2023-08"."""
    m = parse.TITLE.match(text.strip())
    return f"{m[2]}-{scans.MONTHS.index(m[1]) + 1:02d}" if m else None


def _confirm(reports: list[dict], checks: dict) -> None:
    """The handle comparison's year-to-date figures and changes from a year before, which no
    check within a month covers: a scan's figure two engines didn't read alike is taken from
    its readings only where the month before (the year to date runs on from it) or the same
    month a year before (the change) confirms one; otherwise it's left empty. A figure that
    contradicts them is listed (a scan's is left empty)."""
    by_month = {r["month"]: r for r in reports}

    def near(a, b, tol):
        return a is not None and b is not None and abs(a - b) <= tol

    for r in reports:
        y, m = int(r["month"][:4]), int(r["month"][5:])
        prev = by_month.get(f"{y}-{m - 1:02d}") if m > 1 else None
        last_year = by_month.get(f"{y - 1}-{m:02d}")
        scan = r.get("read", "").startswith("ocr")
        for name, line in r["handle_comparison"].items():
            want = {}
            if m == 1:
                want["handle_year_to_date"] = line["handle"]
            elif prev:
                before = prev["handle_comparison"].get(name)
                if before is None:  # sports wagering's first month
                    want["handle_year_to_date"] = line["handle"]
                elif before["handle_year_to_date"] is not None:
                    want["handle_year_to_date"] = before["handle_year_to_date"] + line["handle"]
            if last_year:
                old = last_year["handle_comparison"].get(name)
                for field, ours, theirs in (
                    ("change_from_a_year_before", "handle", "handle"),
                    ("year_to_date_change", "handle_year_to_date", "handle_year_to_date"),
                ):
                    a, b = line[ours], old[theirs] if old else 0
                    if a is not None and b is not None:
                        want[field] = round(100 * (a / b - 1), 2) if b else None
            for field, expected in want.items():
                tol = 0.06 if field == "handle_year_to_date" else 0.011
                value = line[field]
                options = r.get("unconfirmed", {}).get((name, field))
                if options is not None and value is None:
                    found = [o for o in options if (o is None and expected is None)
                             or near(o, expected, tol)]  # fmt: skip
                    if found:
                        line[field] = found[0]
                        r.setdefault("confirmed_by_other_months", []).append(f"{name} {field}")
                    continue
                if (value is None) != (expected is None) or (
                    value is not None and not near(value, expected, tol)
                ):
                    checks["handle comparison doesn't match other months"].append(
                        {"month": r["month"], "section": name, "field": field,
                         "printed": value, "expected": expected,
                         "read": r.get("read")})  # fmt: skip
                    if scan:
                        line[field] = None


def build(files: list[dict], read, raw: Path = RAW) -> tuple[dict[str, pd.DataFrame], dict]:
    checks: dict[str, list] = defaultdict(list)
    reports = []

    def add(r, how, problems, f, label, pages=None):
        for p in problems:
            checks["doesn't add up" if r else "left out"].append(
                {"report": label, "read": how, "problem": p}
            )
        if r:
            reports.append({**r, "read": how, "file": f["file"], "source_url": f["url"],
                            "pages": pages})  # fmt: skip

    for f in files:
        if f["file"].startswith("monthly/"):
            r, how, problems = read_month(read(f["file"]), path=raw / f["file"],
                                          month=month_of(f["text"]))  # fmt: skip
            add(r, how, problems, f, f["text"])
        elif re.fullmatch(r"annual/Gaming-Statistics-\d{4}\.pdf", f["file"]):
            # a year's monthly summaries, scanned and bound together
            path = raw / f["file"]
            for month, pages in scans.months_in(path).items():
                r, how, problems = scans.read_scan(path, pages, month)
                add(r, how, problems, f, f"{f['text']}: {month}",
                    "-".join(str(p + 1) for p in (pages[0], pages[-1]))
                    if len(pages) > 1 else str(pages[0] + 1))  # fmt: skip
    reports.sort(key=lambda r: r["month"])
    _confirm(reports, checks)

    def rows(key, name_as):
        out = []
        for r in reports:
            for x in r[key]:
                out.append({"month": r["month"], name_as: x["name"],
                            **{k: v for k, v in x.items() if k != "name"},
                            "read": r["read"], "source_url": r["source_url"],
                            "pages": r["pages"]})  # fmt: skip
        return pd.DataFrame(out)

    monthly = []
    for r in reports:
        hc = r["handle_comparison"]
        row = {"month": r["month"]}
        for label, key in (("slots", "slots_total"), ("table_games", "table_games_total"),
                           ("sports_wagering", "sports_wagering_total")):  # fmt: skip
            for k, v in (r[key] or {}).items():
                if k != "name":
                    row[f"{label}_{k}"] = v
        row["total_handle"] = hc["Total Handle"]["handle"]
        for name in [n for n in [*SECTIONS, "Total Handle"] if n in hc]:
            slug = name.lower().replace(" ", "_")
            row[f"{slug}_year_to_date"] = hc[name]["handle_year_to_date"]
            row[f"{slug}_change_from_a_year_before"] = hc[name]["change_from_a_year_before"]
        row.update(r["tax"])
        row.update({"read": r["read"], "source_url": r["source_url"], "pages": r["pages"]})
        monthly.append(row)
    monthly_df = pd.DataFrame(monthly)

    by_month = {r["month"]: r for r in reports}
    expected = [f"{y}-{m:02d}" for y in range(2018, 2100) for m in range(1, 13)
                if reports and f"{y}-{m:02d}" <= reports[-1]["month"]]  # fmt: skip
    for month in expected:
        if month not in by_month:
            checks["months missing"].append({"month": month})
    tables = {
        "slots": rows("slots", "denomination"),
        "table_games": rows("table_games", "game"),
        "sports": rows("sports", "sport"),
        "monthly": monthly_df,
    }
    summary = next((f for f in files if f["file"] == SUMMARY), None)
    if summary:
        games, years = annual.read(read(summary["file"]))
        for found in annual.checks(games, years):
            checks["yearly summary doesn't add up"].append(found)
        for t in (games, years):
            for row in t:
                row["source_url"] = summary["url"]
        tables["annual_by_game"] = pd.DataFrame(games)
        tables["annual"] = pd.DataFrame(years)
        for found in against_summary(tables, games, years):
            checks["months don't add up to the yearly summary"].append(found)
    return tables, dict(checks)


SUMMARY = "annual/Gaming-Statistics-1989---2019.pdf"
GAMES = {"BLACK JACK": "Black Jack", "HOUSE BANKED POKER": "House Banked Poker",
         "PLAYER BANKED POKER": "Player Banked Poker", "CRAPS": "Craps",
         "ROULETTE": "Roulette", "KENO": "Keno"}  # fmt: skip


def against_summary(tables: dict, games: list[dict], years: list[dict]) -> list[str]:
    """Each year the monthly reports and the yearly summary both cover (2018, 2019), with
    all twelve months read: the months' sums against the summary, game by game. Player-banked
    poker's Drop is printed equal to its revenue (the house's rake), so only its revenue is
    compared; the summary's HANDLE is the months' total handle."""
    out = []
    monthly, slots, tg = tables["monthly"], tables["slots"], tables["table_games"]
    for y in years:
        year = str(y["year"])
        months = monthly[monthly.month.str.startswith(year)] if len(monthly) else monthly
        if len(months) != 12:
            continue
        pairs = [("HANDLE", y["handle"], months.total_handle.sum()),
                 ("slot machine revenue", y["slot_machine_revenue"],
                  slots[slots.month.str.startswith(year)].statistical_win.sum()),
                 ("table game revenue", y["table_game_revenue"],
                  tg[tg.month.str.startswith(year)].statistical_win.sum()),
                 ("slot machines' coins in",
                  sum(g["coins_in"] or 0 for g in games
                      if g["year"] == y["year"] and g["kind"] == "slot machines"),
                  slots[slots.month.str.startswith(year)].handle.sum())]  # fmt: skip
        for g in games:
            if g["year"] != y["year"] or g["game"] not in GAMES:
                continue
            rows = tg[tg.month.str.startswith(year) & (tg.game == GAMES[g["game"]])]
            pairs.append((f"{g['game']} revenue", g["gross_revenue"], rows.statistical_win.sum()))
            if g["game"] != "PLAYER BANKED POKER":
                pairs.append((f"{g['game']} drop", g["drop"], rows.handle.sum()))
        for label, theirs, ours in pairs:
            if theirs is None or abs(theirs - ours) > 0.011:
                out.append(f"{year} {label}: summary {theirs}, months {ours:,.2f}")
    return out


def main() -> None:
    files = json.loads((RAW / "files.json").read_text())
    tables, checks = build(files, lambda name: (RAW / name).read_bytes())
    BUILD.mkdir(parents=True, exist_ok=True)
    for name, df in tables.items():
        df.to_parquet(BUILD / f"{name}.parquet", index=False)
        df.to_csv(BUILD / f"{name}.csv", index=False)
        period = df["month"] if "month" in df else df["year"]
        print(f"{name}: {len(df):,} rows ({period.min()} to {period.max()})")
    (BUILD / "checks.json").write_text(json.dumps(checks, indent=1, default=str))
    for name, found in checks.items():
        print(f"  {name}: {len(found)}")
        for item in found[:8]:
            print(f"    {item}")


if __name__ == "__main__":
    main()
