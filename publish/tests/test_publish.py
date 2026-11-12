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


def test_build(tmp_path, monkeypatch):
    monkeypatch.setattr(maps, "county_svg", lambda: '<svg><path data-geoid="x"/></svg>')
    vr = tmp_path / "vr"
    vr.mkdir()
    (vr / "chart.json").write_text(json.dumps(CHART))
    out = tmp_path / "site"
    health = build.build(out, vr)
    assert health["pages"]["voter-registration"]["latest_report"] == "2026-10-02"
    page = (out / "voter-registration" / "index.html").read_text()
    assert "October 2, 2026" in page and 'data-geoid="x"' in page
    assert 'href="../static/site.css' in page  # relative, so it works under any root
    assert json.loads((out / "voter-registration" / "data.json").read_text()) == CHART
    home = (out / "index.html").read_text()
    assert 'href="voter-registration/"' in home and "2 reports since 1976" in home
    assert (out / ".nojekyll").exists() and (out / "_health.json").exists()
