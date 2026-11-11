"""Motor vehicle registrations: real pages of the 2026 and historical reports (the state,
Aurora and Oglala Lakota; checked by hand), the statistics page's listing, and a build."""

import json
from pathlib import Path

import build
import fetch
import parse
import pytest

FIXTURES = Path(__file__).parent / "fixtures"
Y2026 = (FIXTURES / "2026-sample.pdf").read_bytes()
HIST = (FIXTURES / "historical-sample.pdf").read_bytes()


def test_the_listing():
    found = fetch.reports(json.loads((FIXTURES / "listing.json").read_text()))
    assert set(found) == {"historical", *map(str, range(2018, 2027))}
    assert found["historical"].endswith(".pdf") and found["2026"].startswith("https://dor.sd.gov/")


def test_a_calendar_year_report():
    rows = parse.read(Y2026)
    assert {r["area"] for r in rows} == {"State Totals", "Aurora", "Oglala Lakota"}
    aurora = {r["label"]: r for r in rows if r["area"] == "Aurora"}
    assert len(aurora) == 13
    assert aurora["1"]["passenger_low_speed"] == 83 and aurora["1"]["titles"] == 107
    assert aurora["Total"]["pickup_suv_van"] == 2811 and aurora["10"]["truck"] == 0
    assert build.unreported_months([r for r in rows if r["area"] == "Aurora"]) == {10, 11, 12}


def test_the_historical_report():
    rows = parse.read(HIST)
    aurora = {r["label"]: r for r in rows if r["area"] == "Aurora"}
    assert sorted(aurora) == [f"CY {y}" for y in range(2015, 2026)]
    assert (
        aurora["CY 2025"]["pickup_suv_van"] == 3337
        and aurora["CY 2015"]["passenger_low_speed"] == 1439
    )


def test_a_changed_layout_stops_the_build():
    numbers = " ".join(str(n) for n in range(1, 16))
    text = ("Titles & Registrations: Aurora\nVehicle Registrations\nIRP\n"
            f"Month Passenger Pickup Gross Titles\n1 {numbers}")  # fmt: skip
    with pytest.raises(parse.LayoutChanged):
        parse.page_rows(text)


def test_the_build():
    annual, monthly, checks = build.build({"2026": Y2026, "historical": HIST},
                                          {"2026": "u2026", "historical": "uh"})  # fmt: skip
    ogl = annual[(annual.area == "Oglala Lakota")]
    assert set(ogl.geoid) == {"0500000US46102"}  # not Shannon's retired 46113
    y2025 = annual[
        (annual.year == 2025) & (annual.area == "Aurora") & (annual.measure == "pickup_suv_van")
    ]
    assert y2025.value.tolist() == [3337] and y2025.report.tolist() == ["historical"]
    y2026 = annual[
        (annual.year == 2026) & (annual.area == "Aurora") & (annual.measure == "pickup_suv_van")
    ]
    assert y2026.value.tolist() == [2811] and y2026.year_to_date.tolist() == [True]
    assert y2026.through_month.tolist() == [9]
    assert sorted(monthly[monthly.area == "Aurora"].month.unique()) == list(range(1, 10))
    assert "months don't add up to the Total" not in checks
    assert checks["areas missing"]  # a sample has 2 of 66 counties
