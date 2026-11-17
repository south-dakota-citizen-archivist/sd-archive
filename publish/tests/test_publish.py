"""The public site's build: the pages, their data and _health.json, from a small chart."""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
sys.modules.pop("build", None)

import build  # noqa: E402
import maps  # noqa: E402

CHART = {
    "updated": "2026-10-03",
    "dates": ["1976-05-17", "2026-10-02"],
    "sources": ["https://example/archived/1976-05-17.pdf", "https://sdsos.gov/x/10.2.2026.pdf"],
    "archived": ["https://example/archived/1976-05-17.pdf", "https://example/2026-10-02.pdf"],
    "elections": ["primary", None],
    "parties": ["republican", "democratic", "other"],
    "state": {"republican": [10, 20], "democratic": [9, 8], "other": [1, 5]},
    "counties": {"0500000US46081": {"name": "Lawrence", "republican": [3, 6],
                                    "democratic": [2, 2], "other": [0, 1]}},
}  # fmt: skip


MEASURES = ["passenger_low_speed", "pickup_suv_van", "truck", "bus", "trailer", "motorcycle",
            "off_road", "recreational", "snowmobile", "boat", "commercial_plate",
            "gross_vehicle_weight", "irp_with_prorate_plate", "electric", "titles"]  # fmt: skip
VEHICLES = {
    "updated": "2026-11-17", "years": [2024, 2025], "measures": MEASURES,
    "vehicle_types": MEASURES[:10], "year_sources": ["https://dor/h.pdf", "https://dor/h.pdf"],
    "state": {m: [100, 110] for m in MEASURES},
    "counties": {"0500000US46081": {"name": "Lawrence", **{m: [10, 12] for m in MEASURES}}},
    "partial": {"year": 2026, "through_month": 9, "source": "https://dor/2026.pdf",
                "current": {"state": {m: 80 for m in MEASURES}, "counties": {}},
                "previous": None},
}  # fmt: skip
POPULATION = """geography,area,geoid,year,population,vintage,source_url
state,South Dakota,0400000US46,2023,900000,2023,u
county,Lawrence County,0500000US46081,2023,28000,2023,u
state,South Dakota,0400000US46,2024,910000,2024,u
county,Lawrence County,0500000US46081,2024,28500,2024,u
"""


def test_build(tmp_path, monkeypatch):
    monkeypatch.setattr(maps, "county_svg", lambda: '<svg><path data-geoid="x"/></svg>')
    vr, mv = tmp_path / "vr", tmp_path / "mv"
    vr.mkdir()
    mv.mkdir()
    (vr / "chart.json").write_text(json.dumps(CHART))
    (mv / "chart.json").write_text(json.dumps(VEHICLES))
    pop = tmp_path / "population.csv"
    pop.write_text(POPULATION)
    monkeypatch.setattr(build, "POPULATION", pop)
    monkeypatch.setattr(build.population, "__defaults__", (pop,))
    out = tmp_path / "site"
    health = build.build(out, {"voter-registration": vr, "vehicle-registration": mv})
    assert health["pages"]["voter-registration"]["latest_report"] == "2026-10-02"
    page = (out / "voter-registration" / "index.html").read_text()
    assert "October 2, 2026" in page and 'data-geoid="x"' in page
    assert 'href="../static/site.css' in page  # relative, so it works under any root
    assert '"sda": "../static/sda.js' in page and 'type="module" src="app.js' in page
    assert (out / "static" / "sda.js").exists()
    assert json.loads((out / "voter-registration" / "data.json").read_text()) == CHART
    home = (out / "index.html").read_text()
    assert 'href="voter-registration/"' in home and "2 reports since 1976" in home
    assert 'href="vehicle-registration/"' in home and "2024–2025 and 2026 so far" in home
    assert (out / ".nojekyll").exists() and (out / "_health.json").exists()
    assert 'href="../voter-registration/" aria-current="page"' in page  # the navbar
    about = (out / "about" / "index.html").read_text()
    assert 'href="../about/" aria-current="page"' in about

    vehicles = (out / "vehicle-registration" / "index.html").read_text()
    assert "through September" in vehicles and 'option value="electric"' in vehicles
    data = json.loads((out / "vehicle-registration" / "data.json").read_text())
    # 2025 has no estimate yet: it takes 2024's, and the page says so
    assert data["population"]["estimated_from"] == [2024, 2024]
    assert data["population"]["counties"]["0500000US46081"] == [28500, 28500]
    assert "years after 2024 use the 2024 estimate" in vehicles
    assert health["pages"]["vehicle-registration"]["through"] == "September 2026"


def test_population_takes_each_years_estimate(tmp_path):
    pop = tmp_path / "population.csv"
    pop.write_text(POPULATION)
    got = build.population([2023, 2024], pop)
    assert got["estimated_from"] == [2023, 2024] and got["state"] == [900000, 910000]
