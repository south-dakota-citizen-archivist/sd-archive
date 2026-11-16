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
from checks import SECTIONS, report_checks  # noqa: F401  (report_checks: the tests)

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
RAW = REPO / ".cache" / "gaming" / "raw"
BUILD = REPO / ".cache" / "gaming" / "build"
BY_HAND = HERE / "fixes.toml"  # decisions from the review pages (sddocs.review)


def read_month(
    content: bytes,
    path: Path | None = None,
    month: str | None = None,
    pages: list[int] | None = None,
    decided: dict | None = None,
    attempt: dict | None = None,
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
        return scans.read_scan(path, pages, month, decided=decided, attempt=attempt)


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
                by_engine = r.get("unconfirmed", {}).get((name, field))
                options = [v for vs in (by_engine or {}).values() for v in vs]
                hand = f"handle_comparison|{name}|{field}" in r.get("by_hand", {})
                if by_engine is not None and value is None and not hand:
                    found = [o for o in options if (o is None and expected is None)
                             or near(o, expected, tol)]  # fmt: skip
                    if found:
                        line[field] = found[0]
                        r.setdefault("confirmed_by_other_months", []).append((name, field))
                    continue
                if (value is None) != (expected is None) or (
                    value is not None and not near(value, expected, tol)
                ):
                    checks["handle comparison doesn't match other months"].append(
                        {"month": r["month"], "section": name, "field": field,
                         "printed": value, "expected": expected,
                         "read": r.get("read")})  # fmt: skip
                    if scan and not hand:  # a figure settled by hand is kept, and noted
                        line[field] = None


def build(files: list[dict], read, raw: Path = RAW,
          by_hand: Path = BY_HAND) -> tuple[dict[str, pd.DataFrame], dict]:  # fmt: skip
    from sddocs import review

    checks: dict[str, list] = defaultdict(list)
    reports, queue = [], []
    decisions: dict[str, dict] = {}
    for (month, field), d in review.load(by_hand).items():
        decisions.setdefault(month, {})[field] = d

    def add(r, how, problems, f, label, month, pages, attempt):
        for p in problems:
            kind = ("doesn't add up, settled by hand" if r and r.get("by_hand")
                    else "doesn't add up" if r else "left out")  # fmt: skip
            checks[kind].append({"report": label, "read": how, "problem": p})
        if r:
            reports.append({**r, "read": how, "file": f["file"], "source_url": f["url"],
                            "pages": ("-".join(str(p + 1) for p in (pages[0], pages[-1]))
                                      if pages and len(pages) > 1
                                      else str(pages[0] + 1) if pages else None)})  # fmt: skip
        elif attempt:
            shown = pages or list(range(_page_count(raw / f["file"])))
            queue.append(scans.table_item(
                month, title=_title(month), subtitle=f"{f['text']}, " + (
                    f"pages {shown[0] + 1}–{shown[-1] + 1}" if len(shown) > 1
                    else f"page {shown[0] + 1}"),
                pdf=str((raw / f["file"]).relative_to(REPO)), pages=shown, url=f["url"],
                attempt=attempt))  # fmt: skip

    for f in files:
        if f["file"].startswith("monthly/"):
            month, attempt = month_of(f["text"]), {}
            r, how, problems = read_month(read(f["file"]), path=raw / f["file"],
                                          month=month, decided=decisions.get(month),
                                          attempt=attempt)  # fmt: skip
            add(r, how, problems, f, f["text"], month, None, attempt)
        elif re.fullmatch(r"annual/Gaming-Statistics-\d{4}\.pdf", f["file"]):
            # a year's monthly summaries, scanned and bound together
            path = raw / f["file"]
            for month, pages in scans.months_in(path).items():
                attempt = {}
                r, how, problems = scans.read_scan(path, pages, month,
                                                   decided=decisions.get(month),
                                                   attempt=attempt)  # fmt: skip
                add(r, how, problems, f, f"{f['text']}: {month}", month, pages, attempt)
    reports.sort(key=lambda r: r["month"])
    _confirm(reports, checks)
    queue += _unsettled(reports, raw)
    tables_review = sorted(queue, key=lambda i: i.key)

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
        row.update({"read": r["read"], "source_url": r["source_url"], "pages": r["pages"],
                    "by_hand": "; ".join(f"{f}: {why}" for f, why in
                                         (r.get("by_hand") or {}).items()) or None})  # fmt: skip
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
    tables["review"] = tables_review
    return tables, dict(checks)


def _title(month: str) -> str:
    return f"{scans.MONTHS[int(month[5:]) - 1]} {month[:4]}"


def _page_count(path: Path) -> int:
    import pdfplumber

    with pdfplumber.open(path) as pdf:
        return len(pdf.pages)


def _unsettled(reports: list[dict], raw: Path) -> list:
    """The handle comparison figures a scan's engines read differently and no other month
    settled, for the review page."""
    from sddocs import review

    out = []
    for r in reports:
        for (name, field), by_engine in (r.get("unconfirmed") or {}).items():
            settled = (name, field) in r.get("confirmed_by_other_months", [])
            if settled or r["handle_comparison"][name].get(field) is not None or not by_engine:
                continue
            readings = {}
            for engine, values in by_engine.items():
                for i, v in enumerate(values):
                    readings[engine if i == 0 else f"{engine} ({i + 1})"] = v
            pages = [int(p) - 1 for p in r["pages"].split("-")] if r.get("pages") else None
            shown = (list(range(pages[0], pages[-1] + 1)) if pages
                     else list(range(_page_count(raw / r["file"]))))  # fmt: skip
            out.append(review.Item(
                source="gaming", key=r["month"], kind="pick", title=_title(r["month"]),
                subtitle=f"handle comparison, read {r['read'][:40]}…",
                links=[{"label": "the report", "url": r["source_url"]}],
                image={"pdf": str((raw / r["file"]).relative_to(REPO)), "pages": shown},
                field=f"handle_comparison|{name}|{field}",
                label=f"{name}: {field.replace('_', ' ')}", readings=readings,
                problems=["The engines read it differently, and neither the month before nor "
                          "the same month a year before settles it."],
                context={"kind": scans.KINDS[field]}))  # fmt: skip
    return out


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
    from sddocs import review

    items = tables.pop("review")
    review.queue(BUILD / "review.json", source="gaming", name="Licensed gaming",
                 decisions=str(BY_HAND.relative_to(REPO)),
                 check=str((HERE / "checks.py").relative_to(REPO)), items=items)  # fmt: skip
    print(f"review: {len(items)} waiting (review.json)")
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
