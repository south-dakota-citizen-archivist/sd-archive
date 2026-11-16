"""What a month's report has to add up to (build.py's checks, and the review page's).

    uv run python pipelines/gaming/checks.py < cells.json

As a script: reads a review item's cells as JSON ({"key": "2022-04", "cells": {field:
value}}, fields as scans.cells names them) and prints the problems with them as a JSON list.
"""

from __future__ import annotations

import json
import sys

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


def main() -> None:
    import scans

    found = json.load(sys.stdin)
    print(json.dumps(scans.problems(scans.from_cells(found["key"], found["cells"]))))


if __name__ == "__main__":
    main()
