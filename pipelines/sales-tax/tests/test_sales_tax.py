"""Municipal sales tax by city and SIC: a month's workbook trimmed to two whole sections (a
city, and a non-city taxing entity), with values checked by hand against the original."""

import sys
from pathlib import Path

import pytest

# The pipeline's scripts import each other as top-level modules (they run as scripts), and
# other pipelines have scripts by the same names (build.py, parse.py), so this pipeline's
# folder goes first on the path and any same-named modules already imported are set aside
# before importing its own (each test module keeps the ones it imported).
_HERE = str(Path(__file__).resolve().parents[1])
sys.path.insert(0, _HERE)
for _name in ("build", "parse", "fetch"):
    sys.modules.pop(_name, None)

import build  # noqa: E402
import fetch  # noqa: E402
import parse  # noqa: E402

SAMPLE = (Path(__file__).parent / "fixtures" / "2026-01-sample.xlsx").read_bytes()


def test_month_of():
    assert fetch.month_of("https://dor.sd.gov/media/x/0126-sd-municipal-by-sic.xlsx") == "2026-01"
    assert fetch.month_of("https://dor.sd.gov/media/x/10-22-sd-municipal-by-sic.xlsx") == "2022-10"
    assert fetch.month_of("https://x/0125municipalmonthlytaxbysic.pdf") == "2025-01"
    assert fetch.month_of("https://dor.sd.gov/media/x/fy26et-state-sic.pdf") is None


def test_listing():
    html = """<main><h2>2026 Sales Tax Statistical Reports</h2>
    <h4>Municipal Taxable Sales per City by SIC Code</h4>
    <a href="/media/a/0126municipalmonthlytaxbysic.pdf">January (PDF)</a>
    <a href="/media/b/0126-sd-municipal-by-sic.xlsx">(XLSX)</a>
    <h4>Tourism Tax Report</h4><a href="/media/c/0126county-tourism.pdf">January (PDF)</a></main>"""
    files = fetch.listing(html)
    assert [f["report"] for f in files] == [fetch.MUNICIPAL_BY_SIC] * 2 + ["Tourism Tax Report"]
    assert fetch.municipal_by_sic(files) == {
        "2026-01": "https://dor.sd.gov/media/b/0126-sd-municipal-by-sic.xlsx"
    }


def test_read():
    rows = parse.read(SAMPLE, "2026-01", "2026-01.xlsx")  # starts with a repeated heading row
    quinn = [r for r in rows if r["city"] == "Quinn"]
    catalog = next(r for r in quinn if r["code"] == "5961")
    assert (catalog["tax"], catalog["division"], catalog["major_group"]) == ("General", "G", "59")
    assert (catalog["taxable"], catalog["tax_due"]) == (211679.66, 2116.79)
    totals = {(r["tax"], r["level"]): r["tax_due"] for r in quinn
              if r["level"] in ("tax total", "city total")}  # fmt: skip
    assert totals == {("General", "tax total"): 3631.99, ("Gross Receipts", "tax total"): 3.0,
                      (None, "city total"): 3634.99}  # fmt: skip
    suppressed = next(r for r in quinn if r["suppressed"])
    assert suppressed["taxable"] is None and suppressed["description"] == "Other"
    wins = [r for r in rows if r["city"] == "DENR - WINS"]  # not the last column's heading
    assert {r["tax"] for r in wins} == {"Gross Receipts", None}


def test_unknown_line():
    import io

    import openpyxl

    wb = openpyxl.Workbook()
    wb.active.append(list(parse.HEADER))
    wb.active.append(["Quinn - General", None, None, None, None, "Quinn"])
    wb.active.append(["Something new", None, "$1.00", "$0.02", None, "Quinn"])
    buf = io.BytesIO()
    wb.save(buf)
    with pytest.raises(parse.LayoutChanged):
        parse.read(buf.getvalue(), "2026-01", "x.xlsx")


def test_build():
    tables, checks = build.build({"2026-01": "https://example/2026-01.xlsx"}, lambda m: SAMPLE)
    for label in ("a major group's lines ≠ its total", "a division's major groups ≠ its total",
                  "a tax's recap divisions ≠ its total", "a city's taxes ≠ its total tax due",
                  "a city's recap divisions ≠ its total tax due"):  # fmt: skip
        assert label not in checks
    taxes = tables["city_taxes"]
    quinn = taxes[taxes.city == "Quinn"].set_index("tax").tax_due.to_dict()
    assert quinn == {"General": 3631.99, "Gross Receipts": 3.0, "all": 3634.99}
    assert taxes[taxes.city == "Quinn"].city_geoid.iloc[0].startswith("1600000US46")
    assert [c["city"] for c in checks["cities without a place GEOID"]] == ["DENR - WINS"]


def test_a_total_that_doesnt_add_up_is_found():
    rows = parse.read(SAMPLE, "2026-01", "x")
    import pandas as pd

    df = pd.DataFrame(rows)
    df.loc[(df.city == "Quinn") & (df.code == "5961"), "taxable"] += 100
    checks = {"x": []}
    build._sum_check(
        df[df.level.isin(["industry", "other"])],
        df[df.level == "major group"],
        ["month", "city", "tax", "division", "major_group"],
        "x",
        checks,
    )
    assert [(c["major_group"], c["column"]) for c in checks["x"]] == [("59", "taxable")]
