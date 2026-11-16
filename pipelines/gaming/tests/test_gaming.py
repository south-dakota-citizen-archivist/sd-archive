"""Gaming statistics: real monthly summaries (text, the label variants, and a scan read by OCR),
the yearly summary for 1989–2019, and the OCR pieces of three scanned months from the yearly
files (so the readings don't depend on which engines are installed), with values checked by
hand against the PDFs.

- October 2018: rapidocr reads the table games' Totals units "99" as "66"; Tesseract's and
  Vision's "99" is the one the rows add up to.
- June 2021: the tax calculation runs onto page 2 ("9%" and Tax Due).
- November 2022: the units and win columns are printed a little above the names, so the
  rows are read column by column.
"""

import json
import sys
from pathlib import Path

import pytest

# The pipeline's scripts import each other as top-level modules (they run as scripts), and
# other pipelines have scripts by the same names (build.py, parse.py), so this pipeline's
# folder goes first on the path and any same-named modules already imported are set aside
# before importing its own (each test module keeps the ones it imported).
_HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_HERE))
for _name in ("build", "parse", "fetch", "scans", "annual"):
    sys.modules.pop(_name, None)

import annual  # noqa: E402
import build  # noqa: E402
import fetch  # noqa: E402
import parse  # noqa: E402
import scans  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


def report(name: str) -> bytes:
    return (FIXTURES / f"{name}.pdf").read_bytes()


def test_a_month():
    r = parse.read(report("September-2026"))
    assert r["month"] == "2026-09"
    penny = next(s for s in r["slots"] if s["name"] == "1 Cents")
    assert (penny["units"], penny["handle"], penny["statistical_win"], penny["payout_percent"]) == (
        2374,
        137970963.06,
        13148458.60,
        90.47,
    )
    assert r["slots_total"]["units"] == 2776 and r["table_games_total"]["handle"] == 8350145.00
    poker = next(g for g in r["table_games"] if g["name"] == "Player Banked Poker")
    assert poker["payout_percent"] is None  # "N/A": a rake game
    assert r["sports_wagering_total"]["casinos_reporting"] == 7
    assert r["handle_comparison"]["Total Handle"]["handle_year_to_date"] == 1348021764.10
    assert r["tax"]["tax_due"] == 1447563.27 and r["tax"]["less_city_slots"] == -583854.82
    assert r["tax"]["adjustments_amended_returns_and_audits"] == -0.11
    formula = next(s for s in r["sports"] if s["name"] == "FORMULA 1")
    assert formula["payout_percent"] is None and formula["statistical_win"] == -25.00  # #DIV/0!
    assert build.report_checks(r) == []


def test_an_older_month():
    r = parse.read(report("January-2023"))
    assert r["month"] == "2023-01" and r["tax"]["taxable_adjusted_gross_revenue"] == 10400483.90
    assert r["tax"]["adjustments_amended_returns_and_audits"] == -3614.11  # "Amended Returns"
    assert len(r["sports"]) == 12 and build.report_checks(r) == []


def test_adjustment_labels():
    feb = parse.read(report("February-2024"))  # "Audit Adjustments"
    assert feb["tax"]["adjustments_amended_returns_and_audits"] == -10079.44
    apr = parse.read(report("April-2024"))  # the label over two lines, the amount between
    assert apr["tax"]["adjustments_amended_returns_and_audits"] == -1849.22
    assert build.report_checks(feb) == [] and build.report_checks(apr) == []


def test_a_scan_is_read_by_ocr_and_checked(tmp_path, monkeypatch):
    monkeypatch.setattr(scans, "OCR_CACHE", tmp_path)
    content = report("October-2024")
    assert parse.is_scan(content)
    r, how, problems = build.read_month(content, month="2024-10")
    assert how.startswith("ocr (rapidocr, lines within 5 pt") and problems == []
    assert r["slots_total"]["handle"] == 139325652.41 and r["tax"]["tax_due"] == 1147312.26


def test_a_misread_figure_fails_the_checks():
    r = parse.read(report("September-2026"))
    r["slots"][0]["handle"] += 1000  # one digit off
    assert any("slot machines: handle" in p for p in build.report_checks(r))


def test_the_page():
    files = fetch.reports((FIXTURES / "page.html").read_text())
    monthly = [f for f in files if f["file"].startswith("monthly/")]
    assert len(monthly) == 45 and monthly[0]["file"] == "monthly/January-2026.pdf"
    assert monthly[0]["url"].startswith("https://dor.sd.gov/media/")
    assert {f["section"] for f in files} >= {
        "Gaming 2023",
        "Gaming Statistics Previous Calendar Years",
    }
    assert not any("racing" in f["url"] or "license" in f["url"] for f in files)


@pytest.mark.parametrize("bad", ["", "no title here"])
def test_unreadable(bad, monkeypatch):
    monkeypatch.setattr(parse, "lines", lambda content, ocr=None: [[bad], []])
    with pytest.raises(parse.ReportProblem):
        parse.read(b"")


def scanned(month: str, monkeypatch):
    """A month of a yearly file, read from its stored OCR pieces."""
    return scanned_with(month, monkeypatch, None)


def scanned_with(month: str, monkeypatch, decided):
    folder = FIXTURES / "ocr" / month
    monkeypatch.setattr(scans, "engines", lambda: list(scans.ENGINES))
    pages = sorted({int(p.name.split(".")[0]) for p in folder.glob("*.json")})
    return scans.read_scan(Path("unused.pdf"), pages, month, decided=decided,
                           get=lambda pdf, i, engine: json.loads(
                               (folder / f"{i}.{engine}.json").read_text()))  # fmt: skip


def test_a_misread_total_is_taken_from_the_engine_the_rows_agree_with(monkeypatch):
    r, how, problems = scanned("2018-10", monkeypatch)
    assert problems == [] and "table games total from" in how
    assert r["table_games_total"]["units"] == 99  # rapidocr: 66
    assert [g["units"] for g in r["table_games"]] == [41, 27, 18, 5, 8]
    assert r["table_games_total"]["handle"] == 5499833.25
    assert r["sports_wagering_total"] is None  # no sports wagering until September 2021
    assert r["tax"]["adjustments_amended_returns_and_audits"] == 22051.00  # "/Court Settlement"
    assert r["tax"]["to_be_distributed"] == 711822.42  # the label is misspelled on the report


def test_the_tax_calculation_runs_onto_page_two(monkeypatch):
    r, how, problems = scanned("2021-06", monkeypatch)
    assert problems == []
    assert r["tax"]["taxable_adjusted_gross_revenue"] == 12559334.61
    assert r["tax"]["tax_due"] == 1130340.11


def test_columns_printed_out_of_line_are_read_by_column(monkeypatch):
    r, how, problems = scanned("2022-11", monkeypatch)
    assert problems == [] and how.startswith("ocr (rapidocr, by column")
    craps, roulette = r["table_games"][3:5]
    assert (craps["name"], craps["units"], craps["handle"], craps["statistical_win"]) == (
        "Craps", 3, 405224.00, 92379.50)  # fmt: skip
    assert (roulette["units"], roulette["statistical_win"]) == (7, 25227.00)
    assert r["handle_comparison"]["Table Games"]["change_from_a_year_before"] == -11.29


def test_a_figure_no_check_covers_is_confirmed_by_other_months():
    def month(m, handle, ytd, change=None, unconfirmed=None):
        return {"month": m, "read": "ocr", "unconfirmed": unconfirmed or {},
                "handle_comparison": {"Table Games": {
                    "handle": handle, "handle_year_to_date": ytd,
                    "change_from_a_year_before": change, "year_to_date_change": None}}}  # fmt: skip

    reports = [month("2018-06", 100.0, 1000.0), month("2019-05", 200.0, 900.0),
               month("2019-06", 110.0, None, None,
                     {("Table Games", "handle_year_to_date"):
                          {"rapidocr": [101.0], "Tesseract": [1010.0]},
                      ("Table Games", "change_from_a_year_before"):
                          {"rapidocr": [-10.0], "Tesseract": [10.0]}})]  # fmt: skip
    checks = {"handle comparison doesn't match other months": []}
    build._confirm(reports, checks)
    line = reports[2]["handle_comparison"]["Table Games"]
    assert line["handle_year_to_date"] == 1010.0  # 900 + 110
    assert line["change_from_a_year_before"] == 10.0  # 110 / 100 - 1
    assert line["year_to_date_change"] is None and reports[2]["confirmed_by_other_months"]


def test_the_yearly_summary():
    games, years = annual.read(report("Gaming-Statistics-1989---2019"))
    assert len(years) == 31 and len(games) == 31 * 14
    blackjack_1989 = next(g for g in games if g["year"] == 1989 and g["game"] == "BLACK JACK")
    assert (blackjack_1989["units"], blackjack_1989["drop"], blackjack_1989["hold_percent"]) == (
        27, 1687524.70, 18.84)  # fmt: skip
    poker = next(g for g in games if g["year"] == 2018 and g["game"] == "PLAYER BANKED POKER")
    assert poker["avg_daily_hold_per_unit"] is None and "100.00%" in poker["note"]
    assert years[-1]["handle"] == 1149838438.24 and years[-1]["retailers_reporting_revenue"] == 113
    # the summary's own: $3 in 2006's table game revenue, $300 in 2017's total
    assert len(annual.checks(games, years)) == 3


def test_cells_name_every_figure(monkeypatch):
    r, _, _ = scanned("2018-10", monkeypatch)
    cells = scans.cells(r)
    assert cells["table_games|Totals|units"] == 99 and cells["tax|tax_due|amount"] == 800800.22
    assert scans.problems(scans.from_cells("2018-10", cells)) == []


def test_a_month_settled_by_hand(monkeypatch):
    def decided(value):
        return {"table_games|Totals|units": {"value": value, "from": "typed",
                                             "reason": "Totals units printed 99."}}  # fmt: skip

    r, how, problems = scanned_with("2018-10", monkeypatch, decided(99))
    assert problems == [] and "by hand: table_games|Totals|units" in how
    assert r["by_hand"] == {"table_games|Totals|units": "Totals units printed 99."}
    # a hand reading the checks don't bear out is still used, and what doesn't add up noted
    r, how, problems = scanned_with("2018-10", monkeypatch, decided(98))
    assert r["table_games_total"]["units"] == 98
    assert problems == ["table games: units adds up to 99.00, Totals line 98.00"]


def test_the_check_script():
    import subprocess

    r = parse.read(report("September-2026"))
    cells = json.dumps({"key": "2026-09", "cells": scans.cells(r)})
    found = subprocess.run([sys.executable, str(_HERE / "checks.py")], input=cells,
                           capture_output=True, text=True, check=True)  # fmt: skip
    assert json.loads(found.stdout) == []


def test_a_left_out_month_for_review(monkeypatch):
    attempt = {}
    folder = FIXTURES / "ocr" / "2018-10"
    monkeypatch.setattr(scans, "engines", lambda: ["rapidocr"])  # rapidocr alone: 66
    r, _, problems = scans.read_scan(Path("unused.pdf"), [0], "2018-10", attempt=attempt,
                                     get=lambda pdf, i, e: json.loads(
                                         (folder / f"{i}.{e}.json").read_text()))  # fmt: skip
    assert r is None and attempt["problems"] == problems
    item = scans.table_item("2018-10", "October 2018", "", ".cache/x.pdf", [10], "u", attempt)
    sections = {s["name"]: s for s in item.sections}
    totals = sections["Table games"]["rows"][-1]["cells"]["units"]
    assert totals["field"] == "table_games|Totals|units" and totals["value"] == 66
    assert "Sports wagering" not in sections  # not until September 2021
