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


FIXTURES = Path(__file__).parent / "fixtures"


def test_tourism():
    rows, unread = parse.tourism((FIXTURES / "tourism-2020-01.pdf").read_bytes(), "2020-01", "x")
    assert unread == []
    aurora = [r for r in rows if r["county"] == "Aurora"]
    assert (aurora[0]["code"], aurora[0]["taxable"], aurora[0]["tax_due"]) == (
        "7011",
        91929.93,
        1378.96,
    )
    assert aurora[-1]["description"] == "Other" and aurora[-1]["suppressed"]
    hunting = next(r for r in rows if r["code"] == "971")  # three-digit SIC codes
    assert hunting["county"] == "Brown"
    state = next(r for r in rows if r["level"] == "state total")
    assert (state["taxable"], state["tax_due"]) == (56338338.11, 845077.72)
    assert any(r["county"] == "Out of State" for r in rows)


def test_tourism_build():
    table, checks = build.build_tourism(
        {"2020-01": "https://example/t.pdf"},
        lambda m: (FIXTURES / "tourism-2020-01.pdf").read_bytes(),
    )
    assert "tourism tax due isn't 1.5% of taxable sales" not in checks
    assert "tourism counties add up to more than the state" not in checks
    assert checks["tourism counties without a GEOID"] == [{"county": "Out of State"}]
    assert table[table.county == "Aurora"].county_geoid.iloc[0] == "0500000US46003"


def test_city_tax_due():
    rows, unread = parse.city_tax_due((FIXTURES / "city-tax-due-2020-01.pdf").read_bytes(),
                                      "2020-01", "x")  # fmt: skip
    assert unread == []
    sioux_falls = next(r for r in rows if r["city"] == "Sioux Falls")
    assert (sioux_falls["tax_due"], sioux_falls["tax_due_year_before"],
            sioux_falls["percent_change"], sioux_falls["listing"]) == (
        14533425.17, 13516240.79, 7.53, "largest")  # fmt: skip
    white = next(r for r in rows if r["city"] == "White")  # "-5,499.10-398.68"
    assert (white["tax_due_year_before"], white["percent_change"]) == (-5499.10, -398.68)
    rows, _ = parse.city_tax_due((FIXTURES / "city-tax-due-2020-02.pdf").read_bytes(),
                                 "2020-02", "x")  # fmt: skip
    chip = next(r for r in rows if r["city"] == "Buffalo Chip")  # nothing the year before
    assert chip["tax_due"] == 3825.15 and chip["tax_due_year_before"] is None


def test_city_tax_due_build():
    files = {"2020-01": "city-tax-due-2020-01.pdf", "2020-02": "city-tax-due-2020-02.pdf"}
    table, checks = build.build_city_tax_due(files, lambda m: (FIXTURES / files[m]).read_bytes(),
                                             None)  # fmt: skip
    assert not table.duplicated(["month", "city"]).any()  # the largest cities' table, once
    assert "city tax due: the largest cities' table differs between pages" not in checks
    assert table[(table.month == "2020-01")].city.nunique() == len(table[table.month == "2020-01"])
    assert len(table[table.month == "2020-01"]) > 250
