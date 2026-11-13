"""Gaming statistics: real monthly summaries (text, the label variants, and a scan read by OCR),
with values checked by hand against the PDFs."""

import sys
from pathlib import Path

import pytest

# The pipeline's scripts import each other as top-level modules (they run as scripts), and
# other pipelines have scripts by the same names (build.py, parse.py), so this pipeline's
# folder goes first on the path and any same-named modules already imported are set aside
# before importing its own (each test module keeps the ones it imported).
_HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_HERE))
for _name in ("build", "parse", "fetch"):
    sys.modules.pop(_name, None)

import build  # noqa: E402
import fetch  # noqa: E402
import parse  # noqa: E402

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


def test_a_scan_is_read_by_ocr_and_checked():
    content = report("October-2024")
    assert parse.is_scan(content)
    r, how, problems = build.read_month(content)
    assert how == "ocr (rapidocr)" and problems == []
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
