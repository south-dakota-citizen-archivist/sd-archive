"""Property sales: real rows of both layouts (checked by hand against the files), and a run."""

from pathlib import Path

import pytest

from records.models import Record, RecordSet
from records.run import run_source
from records.sources.property_sales import source

FIXTURES = Path(__file__).parent / "fixtures"
PAGE = (FIXTURES / "data-page.html").read_text()


def test_sales_files():
    files = source.sales_files(PAGE)
    assert sorted(files) == [2021, 2022, 2023, 2024, 2025]
    assert files[2025] == "https://sdproptax.info/DataLink/3/Download?filename=2025_SD_SALES.csv"


def test_old_layout():
    layout, rows = source.rows((FIXTURES / "2021_SD_SALES.csv").read_text(encoding="utf-8-sig"))
    assert layout == "2021" and len(rows) == 4
    key, data, places = source.sale(rows[1], 2021, layout)
    assert key == "2021 AURORA 200165"
    assert data["sale_price"] == 7000 and data["assessed_value"] == 4260
    assert data["ratio"] == 1.64 and data["ratio_is"] == source.PRICE_OVER_ASSESSED
    assert data["assessment_ratio"] == 0.6086  # 4260 / 7000
    assert data["rejected"] is True and data["remarks"] == "NO CONSIDERATION"
    assert data["legal_description"].startswith("LOT 9 THRU 12 BLK 11")
    assert places == ["0500000US46003", "9700000US4655710"]  # Aurora County, Plankinton 01-1
    _, first, _ = source.sale(rows[0], 2021, layout)
    assert first["remarks"].endswith("GRANTOR/ GRANTEE SAME PEOPLE")  # runs of spaces closed up
    assert first["assessment_ratio"] is None  # no sale price
    assert first["published"]["Year"] == "2011"  # kept as published
    _, brown, places = source.sale(rows[2], 2021, layout)
    assert brown["parcels"] == ["11594", "11625"] and places == ["0500000US46013"]  # "OTHER"
    assert brown["rejected"] is False


def test_new_layout():
    layout, rows = source.rows((FIXTURES / "2025_SD_SALES.csv").read_text(encoding="utf-8-sig"))
    assert layout == "2023"
    key, data, places = source.sale(rows[0], 2025, layout)
    assert key == "2025 93509" and places == ["0500000US46003"]
    assert data["ratio"] == 0.4632 and data["ratio_is"] == source.ASSESSED_OVER_PRICE
    assert data["assessment_ratio"] == 0.4632 and data["rejected"] is False
    assert (data["instrument_date"], data["recording_date"]) == ("2024-11-01", "2024-11-04")
    _, many, _ = source.sale(rows[1], 2025, layout)
    assert len(many["parcels"]) == 14 and many["ratio"] is None  # -99: none computed
    assert many["reject_reason"] == "5 - No money exchanged" and many["rejected"] is True
    _, lawrence, _ = source.sale(rows[2], 2025, layout)
    assert lawrence["parcels"] == ["R16374", "R16375"] and lawrence["document"] is None
    _, todd, _ = source.sale(rows[3], 2025, layout)
    assert todd["assessed_value"] is None and todd["ratio"] is None


def test_unknown_layout():
    with pytest.raises(source.LayoutChanged):
        source.rows("Id,County,Price\n1,Aurora,5\n")


class FixtureFetcher:
    store = None

    def request(self, method, url, **kwargs):
        if url == source.PAGE:
            body = PAGE.encode()
        else:
            name = url.rsplit("=", 1)[-1]
            year = int(name[:4])
            body = (FIXTURES / ("2021_SD_SALES.csv" if year < 2023 else "2025_SD_SALES.csv")
                    ).read_bytes()  # fmt: skip
            if year in (2022, 2023, 2024):
                body = body.splitlines(True)[0]  # header only
        return type("R", (), {"content": body, "text": body.decode("utf-8-sig")})()


def test_run(db, sd_all_places):
    RecordSet.objects.create(slug="property-sales", name="Sales", dataset="property-sales")
    run = run_source("property-sales", fetcher=FixtureFetcher())
    assert run.added == 8
    lawrence = Record.objects.get(key="2021 LAWRENCE 201693")
    assert lawrence.data["school_district"] == "40-2"
    assert lawrence.places.filter(geoid="46081").exists()
    assert Record.objects.get(key="2025 99564").data["county"] == "Todd"
