"""Property tax levies: trimmed copies of the Department's files (real rows, each layout),
with values checked by hand against the originals."""

import json
import sys
from pathlib import Path

import pytest

# The pipeline's scripts import each other as top-level modules (they run as scripts), and
# other pipelines have scripts by the same names (build.py, parse.py), so this pipeline's
# folder goes first on the path and any same-named modules already imported are set aside
# before importing its own (each test module keeps the ones it imported).
_HERE = str(Path(__file__).resolve().parents[1])
sys.path.insert(0, _HERE)
for _name in ("build", "parse", "fetch", "report", "hand", "ocr", "checks"):
    sys.modules.pop(_name, None)

import build  # noqa: E402
import fetch  # noqa: E402
import parse  # noqa: E402
import report  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


def read(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def test_listing():
    files = fetch.listing((FIXTURES / "data-page.html").read_text())
    assert len(files) == 32  # the six recaps and PT-74; not the sales files
    pt74 = files["PT74_2025.csv"]
    assert (pt74["kind"], pt74["year"], pt74["last_updated"]) == ("pt74", 2025, "4/7/2026")
    county = files["County Recap-2018.xlsx"]
    assert county["section"] == "County Recap (MS Excel)" and county["last_updated"] == "7/20/2023"
    assert files["Road Dist Recap-2021.xlsx"]["url"].endswith("Road%20Dist%20Recap-2021.xlsx")
    assert fetch.kind_of("2025_SD_SALES.csv") is None


def test_district_fund():
    layout, rows, _ = parse.levies("City_Town Recap-2025.xlsx", 2025,
                                   read("City_Town Recap-2025.xlsx"))  # fmt: skip
    assert layout == "district-fund"
    plankinton = rows[0]
    assert (plankinton["district"], plankinton["fund"]) == ("Plankinton", "General")
    assert (plankinton["valuation"], plankinton["levy"], plankinton["tax"]) == (50663030, 4.883,
                                                                                247388)  # fmt: skip
    assert plankinton["fund_id"] == 914 and plankinton["row"] == 2


def test_school_layouts():
    _, y2025, _ = parse.levies("School Recap-2025.xlsx", 2025, read("School Recap-2025.xlsx"))
    assert y2025[0]["property_class"] == "agricultural" and y2025[0]["fund"] == "General"
    _, y2023, _ = parse.levies("School Recap-2023.xlsx", 2023, read("School Recap-2023.xlsx"))
    assert [r["property_class"] for r in y2023][:3] == ["agricultural", "mobile_home",
                                                         "mobile_home_owner_occupied"]  # fmt: skip
    assert y2023[0]["fund"] is None and y2023[0]["levy"] == 1.32


def test_school_2021_levy_headings_are_swapped():
    _, rows, checks = parse.levies("School Recap-2021.xlsx", 2021, read("School Recap-2021.xlsx"))
    levy = {r["property_class"]: r["levy"] for r in rows}
    # Plankinton: the file's OO_Levy is 12.676 and Oth_Levy 8.076; its Tax only adds up the
    # other way round
    assert levy["owner_occupied"] == levy["mobile_home_owner_occupied"] == 8.076
    assert levy["other"] == levy["mobile_home"] == levy["utility"] == 12.676
    assert all(r["tax_computed"] for r in rows)
    assert checks == []  # the classes add up to the Tax column
    assert round(sum(r["tax"] for r in rows), 0) == 2067087


def test_school_2022():
    _, rows, checks = parse.levies("School Recap-2022.xlsx", 2022, read("School Recap-2022.xlsx"))
    assert checks == [] and len(rows) == 12
    oo = next(r for r in rows if r["property_class"] == "owner_occupied")
    assert (oo["levy"], round(oo["tax"], 2), oo["tax_computed"]) == (7.388, 216302.35, False)


def test_townships():
    _, y2022, checks = parse.levies("Township Recap-2022.xlsx", 2022,
                                    read("Township Recap-2022.xlsx"))  # fmt: skip
    assert [r["fund"] for r in y2022[:3]] == ["BASE", "OPT_OUT", "RB"]
    assert [c["district"] for c in checks] == ["ALTON"]  # 0.858 in funds vs 0.358 total
    layout, y2023, _ = parse.levies("Township Recap-2023.xlsx", 2023,
                                    read("Township Recap-2023.xlsx"))  # fmt: skip
    assert layout == "township-total" and y2023[0]["levy"] == 0.574


def test_old_layouts():
    _, road, _ = parse.levies("Road Dist Recap-2021.xlsx", 2021, read("Road Dist Recap-2021.xlsx"))
    assert [r["district"] for r in road] == ["Twin Rocks", "Spring Creek Acres"]  # empty row out
    _, city, _ = parse.levies("City_Town Recap-2021.xlsx", 2021, read("City_Town Recap-2021.xlsx"))
    assert city[0]["school_district_code"] == "41-2" and city[1]["county"] == "LINCON"


def test_unknown_layout():
    import io

    import openpyxl

    wb = openpyxl.Workbook()
    wb.active.append(["County", "Name", "Levy"])
    buf = io.BytesIO()
    wb.save(buf)
    with pytest.raises(parse.LayoutChanged):
        parse.levies("x.xlsx", 2025, buf.getvalue())


def test_county_recap():
    rows = parse.county_recap("County Recap-2021.xlsx", 2021, read("County Recap-2021.xlsx"))
    assert {r["county"] for r in rows} == {"MARSHALL"}  # "MARSHALL_x000d_\nMarshall" in the cell
    jackson = parse.county_recap("County Recap-2022.xlsx", 2022, read("County Recap-2022.xlsx"))
    gen = next(r for r in jackson if r["county"] == "JACKSON" and r["field"] == "GEN_LEVY")
    assert gen["value"] == 3.021


def test_places():
    assert build.county_geoid("LINCON") == "0500000US46083"  # misspelled in the file
    assert build.county_geoid("STATE SUMMARY") is None
    plankinton = "9700000US4655710"
    assert build.district_geoid("school", "AURORA", "PLANKINTON", "01-1") == plankinton
    assert build.district_geoid("school", "Aurora", "Plankinton", None) == plankinton
    assert build.district_geoid("school", "Aurora", "Corsica - Stickney", None) == (
        "9700000US4680445"
    )
    assert build.district_geoid("city_town", "LINCOLN", "LENNOX RS", None) is None
    assert build.district_geoid("city_town", "Aurora", "Plankinton", None).startswith("1600000US")


def test_build():
    files = {n: {"kind": k, "year": y, "url": f"https://example/{n}"}
             for n in (p.name for p in FIXTURES.iterdir()) if (found := fetch.kind_of(n))
             for k, y in [found]}  # fmt: skip
    tables, checks = build.build(files, read)
    levies = tables["levies"]
    # the older files give the payable year, the state system's the assessment year
    assert set(levies.assessment_year) == {2020, 2021, 2023, 2025}
    assert (levies.payable_year == levies.assessment_year + 1).all()
    assert levies.county_geoid.notna().all()
    plankinton = levies[(levies.district == "Plankinton") & (levies.kind == "city_town")]
    assert plankinton.adds_up.all() and plankinton.district_geoid.notna().all()
    pt74 = tables["pt74"]
    assert len(pt74) == 5  # Turner's row repeated with county ID 2746 left out
    assert [c["county_id"] for c in checks["PT-74 rows repeated but for the county ID (left out)"]
            ] == [2746]  # fmt: skip
    state = tables["county_recap"]
    assert set(state[state.geography == "state"].county_geoid) == {"0400000US46"}
    assert "tax isn't valuation × levy" in checks or levies.adds_up.dropna().all()


def test_years():
    assert parse.years(2022, "school-2022") == {"assessment_year": 2021, "payable_year": 2022}
    assert parse.years(2023, "school-class") == {"assessment_year": 2023, "payable_year": 2024}
    assert parse.years(2022, "county-recap")["payable_year"] == 2022


def test_statistical_reports_listing():
    import json

    listing = json.loads((FIXTURES.parents[2] / "motor-vehicles" / "tests" / "fixtures"
                          / "listing.json").read_text())  # fmt: skip
    reports = fetch.statistical_reports(listing)
    assert sorted(reports) == list(range(2018, 2025))  # 2018 and 2019 are in a second list
    assert reports[2024].endswith("2024property-tax-statistical-report.pdf")


def sample(year: int, part: str = "sample") -> list[str]:
    return report.pages(read(f"statistical-report-{year}-{part}.pdf"))


def test_report_schools():
    rows, unread = report.schools(sample(2024, "schools"), "2024.pdf")
    corsica = rows[0]
    assert (corsica["payable_year"], corsica["county"], corsica["district"]) == (
        2024,
        "Aurora",
        "CORSICA-STICKNEY",
    )
    assert corsica["value_agricultural"] == 196485836 and corsica["levy_other"] == 8.805
    assert corsica["taxes"] == 1071284.77 and unread == []
    plankinton = next(r for r in rows if r["district_number"] == "01-1")
    assert plankinton["value_agricultural"] == 296584968  # = School Recap-2023.xlsx


def test_report_municipalities():
    rows, _ = report.municipalities(sample(2022), "2022.pdf")  # a rotated page
    aberdeen = rows[0]
    assert (aberdeen["municipality"], aberdeen["county"], aberdeen["population"]) == (
        "Aberdeen",
        "Brown",
        26091,
    )
    assert aberdeen["school_general_ag_levy"] == 1.409  # a column only the 2022 report has
    assert aberdeen["taxes_paid"] == 32692128.73
    rows, _ = report.municipalities(sample(2024), "2024.pdf")
    assert "school_general_ag_levy" not in rows[0] and rows[0]["total_rate_other"] == 20.445


def test_report_county_recaps():
    rows, skipped = report.county_recaps(sample(2022), "2022.pdf")
    ag = rows[0]
    assert (ag["county"], ag["description"], ag["valuation"], ag["tax"]) == (
        "Aurora",
        "AG PROPERTY - OUTSIDE CORP. LIMITS",
        699624981,
        5126767.08,
    )
    general = next(r for r in rows if r["description"] == "COUNTY GENERAL")
    assert general["levy"] == 2.011
    assert skipped  # lines with one amount (a valuation or a tax) aren't guessed at
    rows, _ = report.county_recaps(sample(2024), "2024.pdf")
    assert rows[0]["description"] == "Agricultural-Inside Corporate limits"


def test_report_county_tables():
    texts = sample(2018)
    ratios = report.sales_ratios(texts, "2018.pdf", 2018)
    assert len(ratios) == 67  # the counties and the state
    aurora = ratios[0]
    assert (aurora["ratio_ag"], aurora["ratio_non_ag"], aurora["sales_years"]) == (
        55.9,
        94.5,
        "2016, 2017",
    )
    land = report.ag_land(texts, "2018.pdf")
    assert {r["county"] for r in land} >= {"Oglala Lakota", "Union"}  # decimals, no commas
    assert land[0]["dollars_per_acre"] == 1827.01


def test_ocr_township_columns():
    """A scanned township page is read a column at a time: a left-column amount that runs
    up to the page's middle ("$9,700.14") stays on its row."""
    sys.modules.pop("ocr", None)
    import ocr

    page = json.loads((FIXTURES / "ocr-2021-p26-townships.json").read_text())
    text = ocr.page_text(page)
    assert "KENNEDY $39,431,368 0.246 $9,700.14" in text.splitlines()
    rows, _ = report.townships([text], "2021.pdf")
    assert ("KENNEDY", 39431368, 0.246, 9700.14) in [
        (r["township"], r["taxable_value"], r["levy"], r["taxes"]) for r in rows
    ]


def test_ocr_clean():
    sys.modules.pop("ocr", None)
    import ocr

    assert ocr._clean("$o 1,2O4 Baltic-RS ★ OAKLAKE") == "$0 1,204 Baltic-RS * OAKLAKE"
    # periods read for commas in a whole-dollar valuation; S for $; cents left alone
    assert ocr._clean("COUNTY GENERAL $1.369,891.623 4.1 $5,616.55") == (
        "COUNTY GENERAL $1,369,891,623 4.1 $5,616.55")  # fmt: skip
    assert ocr._clean("LIBRARY s0 TELEPHONE S16.239 RS 101-50") == (
        "LIBRARY $0 TELEPHONE $16,239 RS 101-50")  # fmt: skip


def test_report_townships_blank_cells():
    """A township that levied nothing has blank levy and taxes (checked on the 2018 scan);
    2018's unorganized counties have no figures at all."""
    text = "\n".join([
        "TOWNSHIP VALUATION, LEVY, AND TAXES - PAYABLE 2018",
        "BRULE", "CLEVELAND $39,293,251", "EAGLE $35,828,991 0.120 $4,299.55",
        "BENNETT", "ALL UNORGANIZED", "PENNINGTON", "FLAT BUTTE $3,093,937 $0.00",
        "TAXABLE VALUE MILL LEVY TAXES",
    ])  # fmt: skip
    rows, unread = report.townships([text], "2018.pdf")
    assert [(r["county"], r["township"], r["taxable_value"], r["levy"], r["taxes"])
            for r in rows] == [
        ("Brule", "CLEVELAND", 39293251, None, None),
        ("Brule", "EAGLE", 35828991, 0.12, 4299.55),
        ("Bennett", "ALL UNORGANIZED", None, None, None),
        ("Pennington", "FLAT BUTTE", 3093937, None, 0.0),
    ]  # fmt: skip
    assert unread == []


# lines of the scanned 2019 and 2020 municipal tables, as OCR read them
MUNICIPAL_2020 = "\n".join([
    "MUNICIPALITIES - VALUATIONS, LEVIES, AND TAXES - 2019 PAY 2020",
    "Located in School Population County SCHOOL SCHOOL SCHOOL Total Tax Total Tax Total Tax "
    "Total Tax Grand Total",
    "Taxing District Name County District Census Total Valuation City Levy Levy TOTAL AG "
    "TOTAL OO TOTAL OTH RATE AG RATE OO RATE OTH RATE UTIL Taxes",
    # a levy read without its point (2.645)
    "Akaska Walworth 62-5 42 12,061,426 3.995 2645 3.157 5.551 10.182 9.797 12.191 16.822 "
    "16.822 181,964.85",
    # a blank population
    "Arlington Kingsbury 38-1 50,682,264 5.039 1.811 6.782 9.045 13.423 13.632 15.895 20.273 "
    "20.273 894,801.06",
    # whole rates (the report leaves off zeros) and stray digits before the name
    "2 Yankton Yankton 63-3 14454 903,626,027 4.592 3.129 5.927 7.751 11.279 13.648 15.472 "
    "19 19 15,102,832.53",
    # the asterisk read as another mark
    "Iroquois Kingsbury 2-3 × 8,190,016 7.556 1.884 3.885 6.976 12.955 13.325 16.416 22.395 "
    "22.395 148,455.25",
])  # fmt: skip
MUNICIPAL_2019_AMBIGUOUS = "\n".join([
    "Municipalities - Valuations, Levies, and Taxes - 2018 Payable 2019",
    "City Name County District Census Total Valuation City Levy Levy AG 00 OTH RATE AG RATE OO "
    "RATE OTH RATE UTIL Taxes",
    # the city's or the county's levy is blank: the rates can't tell which
    "Badger Kingsbury 38-1 107 6,768,118 1.779 6.728 9.054 13.553 8.507 10.833 15.332 15.332 "
    "$ 81,067.26",
])  # fmt: skip


def test_report_municipalities_scanned():
    rows, unread = report.municipalities([MUNICIPAL_2020], "2020.pdf")
    by_name = {r["municipality"]: r for r in rows}
    assert list(by_name) == ["Akaska", "Arlington", "Yankton", "Iroquois"] and unread == []
    assert by_name["Akaska"]["county_levy"] == 2.645
    assert by_name["Akaska"]["read_as"] == "county_levy 2645 → 2.645"
    arlington = by_name["Arlington"]
    assert (arlington["population"], arlington["valuation"], arlington["city_levy"]) == (
        None, 50682264, 5.039)  # fmt: skip
    assert arlington["read_as"] == "population blank"
    yankton = by_name["Yankton"]
    assert (yankton["population"], yankton["total_rate_other"]) == (14454, 19)
    assert "read_as" not in yankton
    assert by_name["Iroquois"]["population"] is None  # "*": population on the main row
    assert report.rates_check(by_name["Akaska"]) == "adds up"

    rows, unread = report.municipalities([MUNICIPAL_2019_AMBIGUOUS], "2019.pdf")
    assert rows == [] and len(unread) == 1


def test_rates_check():
    row = {"city_levy": 5.083, "county_levy": 1.607, "school_ag_levy": 6.484,
           "school_oo_levy": 8.697, "school_other_levy": 12.976, "total_rate_ag": 13.383,
           "total_rate_oo": 15.596, "total_rate_other": 19.875}  # fmt: skip
    assert report.rates_check(row) == "another levy of 0.209"  # Kingsbury, 2021
    assert report.rates_check(row | {"total_rate_oo": 25.596}) == "doesn't add up"
    assert (
        report.rates_check(
            row | {"county_levy": None, "read_as": "county_levy 1607 → left blank (point lost)"}
        )
        == ""
    )


def test_rows_settled_by_hand():
    import hand

    line = {"file": "2018.pdf", "page": 45, "table": "schools", "county": "Gregory",
            "payable_year": 2018,
            "line": "GREGORY 26-4 $205,129,628 $43,096,593 $26,712,929 5.204 7.480 11.880 "
                    "$1,707,212.46"}  # fmt: skip
    key = hand.key_of_line(line)
    assert key == "school 2018 p45 GREGORY 26-4"
    assert hand.key_of_line({**line, "_n": 2}) == "school 2018 p45 GREGORY 26-4 #2"
    decided = {c: {"value": v, "from": "typed", "reason": "From the page."} for c, v in {
        "value_agricultural": 205129628.0, "value_owner_occupied": 43096593.0,
        "value_other_non_ag": 26712929.0, "value_centrally_assessed": None,
        "levy_agricultural": 5.204, "levy_owner_occupied": 7.48, "levy_other": 11.88,
        "taxes": 1707212.46}.items()}  # fmt: skip
    row = hand.row_from_line(line, decided)
    assert (row["district"], row["district_number"], row["county"]) == ("GREGORY", "26-4",
                                                                         "Gregory")  # fmt: skip
    assert row["read_from"] == "by hand" and row["levy_other"] == 11.88
    assert hand.row_from_line(line, {"*": {"reason": "Not a row."}}) is None
    # the review page's check: these valuations and levies give the taxes printed
    cells = {k: d["value"] for k, d in decided.items()}
    assert hand.problems(key, cells) == []
    assert hand.problems(key, {**cells, "levy_other": 1.188})  # a lost digit doesn't fit


def test_a_municipal_row_settled_by_hand():
    import hand

    row = {"file": "2020.pdf", "page": 16, "municipality": "Hot Springs", "county": "Fall River",
           "school_district_number": "23-2", "payable_year": 2020, "population": 371,
           "valuation": 184049282, "city_levy": 6.427, "county_levy": 4.563,
           "school_ag_levy": 5.849, "school_oo_levy": 7.694, "school_other_levy": 11.262,
           "total_rate_ag": 16.832, "total_rate_oo": 18.684, "total_rate_other": 22.252,
           "taxes_paid": 3709335.66}  # fmt: skip
    key = hand.key_of_municipality(row)
    assert key == "municipality 2020 p16 Hot Springs Fall River 23-2"
    assert hand.problems(key, row)  # 6.427 + 4.563 + 5.849 isn't 16.832
    # (made-up rates: the levies' sums, for a row that adds up once the ag rate is set)
    hand.settle_municipality(row, {"total_rate_ag": {"value": 16.839, "from": "typed",
                                                     "reason": "The sum."}})  # fmt: skip
    assert row["total_rate_ag"] == 16.839 and row["by_hand"] == "total_rate_ag: The sum."
    assert hand.problems(key, row) == []
