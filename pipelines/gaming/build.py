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
from collections import defaultdict
from pathlib import Path

import pandas as pd
import parse  # pipelines/gaming/parse.py (run as a script, so this folder is on the path)

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
RAW = REPO / ".cache" / "gaming" / "raw"
BUILD = REPO / ".cache" / "gaming" / "build"
CENT = 0.011
ADJUSTMENTS = ["less_non_cashable_promo_play", "plus_non_cashable_promo_wins",
               "less_wide_area_progressive", "less_city_slots", "plus_expired_vouchers",
               "less_federal_excise_tax", "adjustments_amended_returns_and_audits"]  # fmt: skip
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

    for rows, total, label in ((r["slots"], r["slots_total"], "slots"),
                               (r["table_games"], r["table_games_total"], "table games"),
                               ([], r["sports_wagering_total"], "sports wagering")):  # fmt: skip
        for x in [*rows, total]:
            if x["payout_percent"] is None or not x["handle"]:
                continue
            want = (payout(rows) if x is total and rows
                    else 100 * (x["handle"] - x["statistical_win"]) / x["handle"])  # fmt: skip
            if abs(want - x["payout_percent"]) > 0.06:
                out.append(f"{label} {x['name']}: payout {x['payout_percent']}%, "
                           f"(handle - win) / handle is {want:.2f}%")  # fmt: skip
    hc = r["handle_comparison"]
    for name, key in SECTIONS.items():
        if not near(hc[name]["handle"], r[key]["handle"]):
            out.append(f"handle comparison: {name} {hc[name]['handle']:,.2f}, section total "
                       f"{r[key]['handle']:,.2f}")  # fmt: skip
    total = sum(hc[n]["handle"] for n in SECTIONS)
    if not near(total, hc["Total Handle"]["handle"], 0.05):
        out.append(f"total handle {hc['Total Handle']['handle']:,.2f}, sections {total:,.2f}")
    t = r["tax"]
    win = sum(r[k]["statistical_win"] for k in SECTIONS.values())
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


def read_month(content: bytes) -> tuple[dict | None, str, list[str]]:
    """A report, how it was read, and what doesn't add up (a scan that fails a check isn't
    used: None)."""
    if not parse.is_scan(content):
        r = parse.read(content)
        return r, "text", report_checks(r)
    try:
        r = parse.read(content, ocr="rapidocr")
    except parse.ReportProblem as e:
        return None, "ocr (rapidocr)", [f"scan not read: {e}"]
    problems = report_checks(r)
    return (None if problems else r), "ocr (rapidocr)", problems


def build(files: list[dict], read) -> tuple[dict[str, pd.DataFrame], dict]:
    checks: dict[str, list] = defaultdict(list)
    reports = []
    for f in files:
        if not f["file"].startswith("monthly/"):
            continue
        r, how, problems = read_month(read(f["file"]))
        for p in problems:
            checks["doesn't add up" if r else "left out"].append(
                {"report": f["text"], "read": how, "problem": p}
            )
        if r:
            reports.append({**r, "read": how, "file": f["file"], "source_url": f["url"]})
    reports.sort(key=lambda r: r["month"])

    def rows(key, name_as):
        out = []
        for r in reports:
            for x in r[key]:
                out.append({"month": r["month"], name_as: x["name"],
                            **{k: v for k, v in x.items() if k != "name"},
                            "read": r["read"], "source_url": r["source_url"]})  # fmt: skip
        return pd.DataFrame(out)

    monthly = []
    for r in reports:
        hc = r["handle_comparison"]
        row = {"month": r["month"]}
        for label, key in (("slots", "slots_total"), ("table_games", "table_games_total"),
                           ("sports_wagering", "sports_wagering_total")):  # fmt: skip
            for k, v in r[key].items():
                if k != "name":
                    row[f"{label}_{k}"] = v
        row["total_handle"] = hc["Total Handle"]["handle"]
        for name in [*SECTIONS, "Total Handle"]:
            slug = name.lower().replace(" ", "_")
            row[f"{slug}_year_to_date"] = hc[name]["handle_year_to_date"]
            row[f"{slug}_change_from_a_year_before"] = hc[name]["change_from_a_year_before"]
        row.update(r["tax"])
        row.update({"read": r["read"], "source_url": r["source_url"]})
        monthly.append(row)
    monthly_df = pd.DataFrame(monthly)

    # the year to date runs on from the month before
    by_month = {r["month"]: r for r in reports}
    for r in reports:
        y, m = int(r["month"][:4]), int(r["month"][5:])
        prev = by_month.get(f"{y}-{m - 1:02d}") if m > 1 else None
        for name in [*SECTIONS, "Total Handle"]:
            hc = r["handle_comparison"][name]
            want = hc["handle"] + (prev["handle_comparison"][name]["handle_year_to_date"]
                                   if prev else 0)  # fmt: skip
            if (m == 1 or prev) and abs(want - hc["handle_year_to_date"]) > 0.06:
                checks["year to date doesn't run on from the month before"].append(
                    {
                        "month": r["month"],
                        "section": name,
                        "year_to_date": hc["handle_year_to_date"],
                        "expected": round(want, 2),
                    }
                )
    expected = [f"{y}-{m:02d}" for y in range(2023, 2100) for m in range(1, 13)
                if reports and f"{y}-{m:02d}" <= reports[-1]["month"]]  # fmt: skip
    for month in expected:
        if month not in by_month:
            checks["months missing"].append({"month": month})
    return {
        "slots": rows("slots", "denomination"),
        "table_games": rows("table_games", "game"),
        "sports": rows("sports", "sport"),
        "monthly": monthly_df,
    }, dict(checks)


def main() -> None:
    files = json.loads((RAW / "files.json").read_text())
    tables, checks = build(files, lambda name: (RAW / name).read_bytes())
    BUILD.mkdir(parents=True, exist_ok=True)
    for name, df in tables.items():
        df.to_parquet(BUILD / f"{name}.parquet", index=False)
        df.to_csv(BUILD / f"{name}.csv", index=False)
        print(f"{name}: {len(df):,} rows ({df.month.min()} to {df.month.max()})")
    (BUILD / "checks.json").write_text(json.dumps(checks, indent=1, default=str))
    for name, found in checks.items():
        print(f"  {name}: {len(found)}")
        for item in found[:8]:
            print(f"    {item}")


if __name__ == "__main__":
    main()
