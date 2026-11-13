"""County map apps: real report pages from four counties (each lays its report out
differently, checked by hand), a real GetVectorLayer answer, the tiling, profiles, and a run
of the source against a stand-in for the browser."""

import json
from pathlib import Path

import pytest
import shapely

from records.sources._map_app import parse, profile, tiles

FIXTURES = Path(__file__).parent / "fixtures"


def page(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def report(name: str) -> dict:
    return parse.report(page(name))


CONFIG = {
    "AppId": 1225, "LayerId": 37418, "SRID": 102734, "Units": "ft",
    "MaxExtent": {"minx": 980000.0, "miny": 215000.0, "maxx": 990000.0, "maxy": 225000.0},
    "DefaultReportUrl": "/Application.aspx?AppID=1225&LayerID=37418&PageTypeID=4&PageID=14291"
                        "&Q=1258345547&KeyValue={0}",
    "LayerView": [{"GroupName": "Base Data", "Layers": [
        {"LayerName": "Parcels", "LayerId": 37418, "MapKey": "PARCEL_ID"},
        {"LayerName": "Roads", "LayerId": 38408, "MapKey": "FID"}]}],
}  # fmt: skip


def test_the_map_configuration_and_report_links():
    html = f"<script>var mapConfig = {json.dumps(CONFIG)};\nfoo();</script>"
    assert parse.map_config(html)["LayerId"] == 37418
    assert parse.report_url(CONFIG, "20600-00602-220-66") == (
        "https://beacon.schneidercorp.com/Application.aspx?AppID=1225&LayerID=37418"
        "&PageTypeID=4&PageID=14291&KeyValue=20600-00602-220-66"
    )


def test_features_shapes_and_tooltips():
    answer = json.loads(page("vector.json"))["d"]
    found = parse.features(answer)
    assert [f["key"] for f in found] == ["17000-00503-280-00", "26020-00000-000-00",
                                         "26055-00000-000-00"]  # fmt: skip
    assert found[2]["tooltip"] == {
        "Owner": "HOMESTAKE MINING CO (Prim)", "Acres": "817.187", "Value": "0",
        "photo": "https://venturi.blob.core.windows.net/fd-4868/2/26055-00000-000-00/0",
    }  # fmt: skip
    shape = shapely.from_wkb(parse.geometry(found[0]["wkt"], 102734))
    assert shape.geom_type == "MultiPolygon"
    lon, lat = shape.centroid.x, shape.centroid.y  # near Lead
    assert -103.8 < lon < -103.7 and 44.3 < lat < 44.4


def test_lawrence():
    r = report("lawrence-report.html")
    summary = r["sections"]["Summary"]
    assert summary["ParcelNumber"] == "20600-00602-220-66"
    assert summary["Property Address"] == "173 PINE CONE AVE"
    assert summary["Zoning"] == "SRD" and summary["Acreage"] == "4.37"
    assert r["sections"]["Valuation"][0] == {
        "Assessed Year": "2026", "ApprLandValue": "$121,850.00",
        "ApprImprovementValue": "$311,350.00", "ApprTotalValue": "$433,200.00",
    }  # fmt: skip
    assert len(r["sections"]["Valuation"]) == 5
    assert r["sections"]["Tax Units"][0] == {"Code": "40-2", "Description": "40-2 Spearfish School",
                                            "Rate": ""}  # fmt: skip
    assert r["owners"] == [{"names": ["HENRY, PETER P & SHARON M"],
                            "address": ["173 PINE CONE AVE", "SPEARFISH,SD 57783"]}]  # fmt: skip
    assert r["photos"] == ["https://venturi.blob.core.windows.net/fd-4868/0/20600-00602-220-66/0"]
    assert "Residential Buildings" in r["restricted"] and "Sales" in r["restricted"]
    assert "Contact Information" not in r["sections"]
    # an owner whose name is a search link
    assert report("lawrence-report-vacant.html")["owners"][0]["names"] == ["SUNNY MEADOWS LLC"]


def test_minnehaha():
    r = report("minnehaha-report.html")
    assert r["owners"][0] == {"names": ["WAXWEILER, ROBERT C &"], "type": "Deed Holder",
                              "address": ["2404 E BREK ST", "BRANDON SD 57005"]}  # fmt: skip
    assert r["sections"]["Sales"][0]["Sale Amount"] == "$383,500"
    assert r["sections"]["Sales"][0]["Buyer"] == "WAXWEILER, ROBERT C"
    assert r["sections"]["Additional Parcels Involved in Sale"][0] == "Seq: 20244823 - 099632"
    taxes = r["sections"]["Taxation"]
    assert taxes[1] == {"year": "2025", "item": "Tax Billed", "1st Half": "270.75",
                        "2nd Half": "270.75", "Full Year": "541.50"}  # fmt: skip
    assert r["sections"]["Valuation"][0]["Total Value"] == "$292,500"
    assert "Recent Sales In Area" not in r["sections"]  # a search form
    assert "Online Tax Payments" not in r["sections"]


def test_moody():
    r = report("moody-report.html")
    assert r["owners"][0] == {"names": ["BOTHERN, RONALD & CHARLENE"], "type": "Deed Holder",
                              "address": ["46709 225TH STREET", "COLMAN, SD 57017"]}  # fmt: skip
    # years across, a "+" column to expand rows and "=" before the total: a row per year
    assert r["sections"]["Valuation"][0] == {"year": "2026", "NACS": "$180,000",
                                             "NAC1S": "$90,678", "NAC1": "$9,110",
                                             "Total Value": "$279,788"}  # fmt: skip


def test_meade():
    r = report("meade-report.html")
    assert list(r["sections"]) == ["Summary"]  # "Need more information?" is log-in buttons
    assert r["sections"]["Summary"]["Parcel ID"] == "21.97.0B"
    assert r["owners"] == [] and "Owners" in r["restricted"]


def test_not_a_report():
    from records.sources._tables import LayoutChanged

    with pytest.raises(LayoutChanged):
        parse.report(b"<html><body>An error has occurred</body></html>")


# --- tiles ---


def fake_layer(points: dict[str, tuple[float, float]], limit: int = 5):
    asked = []

    def ask(box):
        asked.append(box)
        inside = [{"Key": k} for k, (x, y) in points.items()
                  if box["minx"] <= x <= box["maxx"]
                  and box["miny"] <= y <= box["maxy"]]  # fmt: skip
        return inside[:limit]

    return ask, asked


def test_full_tiles_are_split_until_every_feature_is_read():
    points = {f"p{i}": (1000 + i * 50, 1000 + i * 30) for i in range(20)}  # bunched together
    points["far"] = (9500.0, 9500.0)
    ask, asked = fake_layer(points)
    found, counts = tiles.read(ask, {"minx": 0, "miny": 0, "maxx": 10000, "maxy": 10000},
                               n=2, limit=5)  # fmt: skip
    assert set(found) == set(points)
    assert counts["split"] > 0 and counts["tiles"] == len(asked)


def test_tiles_are_kept_and_reused(tmp_path):
    from sdfetch import Store

    store = Store(tmp_path)
    ask, asked = fake_layer({"a": (1, 1), "b": (60, 60)})
    extent = {"minx": 0, "miny": 0, "maxx": 100, "maxy": 100}
    tiles.read(ask, extent, n=2, store=store)
    first = len(asked)
    found, counts = tiles.read(ask, extent, n=2, store=store)
    assert len(asked) == first and counts["reused"] == 4 and set(found) == {"a", "b"}


def test_a_tile_that_cant_be_split_further():
    ask, _ = fake_layer({f"p{i}": (1.0, 1.0) for i in range(10)})
    with pytest.raises(tiles.TooDense):
        tiles.read(ask, {"minx": 0, "miny": 0, "maxx": 100, "maxy": 100}, n=1, limit=5)


# --- profiles ---


def test_a_profile_and_what_differs_from_it():
    reports = [report("lawrence-report.html"), report("lawrence-report-vacant.html")]
    p = profile.build(reports, CONFIG)
    assert p["map"]["layer"] == "Parcels" and p["map"]["key_field"] == "PARCEL_ID"
    assert p["sections"]["Summary"]["kind"] == "fields"
    assert p["sections"]["Summary"]["fields"]["Zoning"] == 2
    assert p["sections"]["Valuation"]["kind"] == "rows"
    assert p["restricted"]["Sales"] == 2 and p["owners"]["pages"] == 2
    assert profile.differences(reports[0], p) == []
    changed = report("lawrence-report.html")
    changed["sections"]["Summary"]["Flood Zone"] = "X"
    changed["sections"]["Easements"] = ["one"]
    assert profile.differences(changed, p) == ["new field: Summary / Flood Zone",
                                               "new section: Easements"]  # fmt: skip


# --- a county's source ---


class FakeSession:
    """Stands in for the browser: the map answers with the fixture's features, and every
    report is the fixture page."""

    reports_asked: list = []

    def __init__(self, app, profile_dir, **kwargs):
        self.config = CONFIG

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def api(self, method, body):
        assert method == "GetVectorLayer" and body["layerId"] == 37418
        return json.loads(page("vector.json"))["d"]

    def report(self, key):
        FakeSession.reports_asked.append(key)
        return page("lawrence-report.html")


def test_a_run(db, sd_counties, monkeypatch, tmp_path):
    from records.models import Record, RecordSet
    from records.run import run_source
    from records.sources._map_app import source as map_app
    from sdfetch import Store

    monkeypatch.setattr(map_app, "Session", FakeSession)
    monkeypatch.setattr(profile, "load", lambda app: None)
    FakeSession.reports_asked = []
    fetcher = type("F", (), {"store": Store(tmp_path)})()
    RecordSet.objects.create(slug="gis-data", name="GIS data", dataset="gis-data")  # fmt: skip
    run = run_source("gis-data", fetcher=fetcher)
    apps = run.checks["apps"]  # each app read in turn, each with its own report pages
    assert run.added == 6 and [a["reports_read"] for a in apps.values()] == [3, 3]
    assert [a["without_report"] for a in apps.values()] == [0, 0]
    assert "stopped" not in run.checks
    record = Record.objects.get(key="LawrenceCountySD:17000-00503-280-00")
    assert record.geometry.geom_type == "MultiPolygon"
    assert record.data["sections"]["Summary"]["Zoning"] == "SRD"
    assert record.data["tooltip"]["Owner"].startswith("HOMESTAKE")
    assert sorted(record.places.values_list("geoid", flat=True)) == ["46", "46081"]
    # the next run in the same month reads nothing again
    run = run_source("gis-data", fetcher=fetcher)
    assert [a["reports_read"] for a in run.checks["apps"].values()] == [0, 0]
    assert len(FakeSession.reports_asked) == 6
