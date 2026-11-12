"""Voter registration: real reports (each layout the parser reads) and the page's list, with
values checked by hand against the PDFs; and the build against the committed snapshots."""

import json
import sys
import tomllib
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


def report(date: str) -> bytes:
    return (FIXTURES / f"report-{date}.pdf").read_bytes()


def test_a_report():
    date, parties, rows = parse.read(report("2026-10-02"))
    assert date == "2026-10-02" and len(rows) == 66
    assert parties == [
        "democratic",
        "libertarian",
        "republican",
        "independent",
        "other",
        "npa",
        "inactive",
    ]  # the report's own order
    aurora = rows[0]
    assert (aurora["county"], aurora["republican"], aurora["democratic"]) == ("Aurora", 1018, 443)


def test_a_report_without_rules():
    """July 31, 2026: no table lines, and blank cells (Bon Homme has no "OTH" figure)."""
    date, parties, rows = parse.read(report("2026-07-31"))
    assert date == "2026-07-31" and "npa" in parties
    bon_homme = next(r for r in rows if r["county"] == "Bon Homme")
    assert (bon_homme["other"], bon_homme["npa"], bon_homme["inactive"]) == (0, 233, 251)


def test_the_title_repeated_on_page_two():
    date, _, rows = parse.read(report("2022-02-01"))
    assert date == "2022-02-01" and len(rows) == 66 and rows[-1]["county"] == "Ziebach"


def test_a_report_that_doesnt_add_up_is_refused():
    rows = [{"county": f"C{i}", "republican": 1, "inactive": 0, "total_active": 1}
            for i in range(66)]  # fmt: skip
    total = {"republican": 66, "inactive": 0, "total_active": 66}
    parse.check(rows, ["republican", "inactive"], total)
    rows[3]["total_active"] = 2  # the column still adds up to the Total row; C3 doesn't
    with pytest.raises(parse.ReportProblem, match="C3"):
        parse.check(rows, ["republican", "inactive"], {**total, "total_active": 67})
    with pytest.raises(parse.ReportProblem, match="65 counties"):
        parse.check(rows[:65], ["republican", "inactive"], total)


def test_the_september_2026_report():
    """First published with Republican and Democratic swapped; the file now on the page (from
    September 14) has them right, and so does the snapshot."""
    date, _, rows = parse.read(report("2026-09-01"))
    assert (rows[0]["county"], rows[0]["democratic"], rows[0]["republican"]) == (
        "Aurora",
        443,
        1019,
    )
    snapshot = (_HERE / "snapshots" / "2026-09-01.csv").read_text().splitlines()
    assert snapshot[1].startswith("2026-09-01,Aurora,443,")
    assert tomllib.loads((_HERE / "fixes.toml").read_text()) == {}  # nothing to correct now


def test_a_swap_correction():
    rows = [{"county": "Aurora", "democratic": 1019, "republican": 443}]
    fixed = fetch.apply_fixes(
        "2026-09-01", rows, {"2026-09-01": {"swap": ["democratic", "republican"]}}
    )
    assert (fixed[0]["democratic"], fixed[0]["republican"]) == (443, 1019)
    assert fetch.apply_fixes("2026-10-02", rows, {}) == rows


def test_the_page():
    links = fetch.reports((FIXTURES / "page.html").read_text())
    assert len(links) == 13  # the content section only, not the navigation
    by_text = {link["text"]: link for link in links}
    assert by_text["October 2, 2026"]["link_date"] == "2026-10-02"
    assert by_text["June 2, 2026 Primary Election"]["link_date"] == "2026-06-02"
    notice = next(link for link in links if "lost party status" in link["text"])
    assert notice["url"].endswith("StatewideVotersByCounty_11.20.2018.pdf")
    assert " " not in notice["url"]  # "Prior to 2026" in the path, encoded


def test_every_listed_report_has_a_snapshot():
    sources = json.loads((_HERE / "sources.json").read_text())
    have = {p.stem for p in (_HERE / "snapshots").glob("*.csv")}
    assert {s["report_date"] for s in sources.values()} <= have


def test_build():
    rows = build.snapshots(_HERE / "snapshots")
    tables, chart, checks = build.build(
        rows,
        build.sources(_HERE / "sources.json"),
        json.loads((_HERE / "elections.json").read_text()),
    )
    assert checks == {}
    long, simple = tables["registration"], tables["registration-simplified"]
    shannon = long[(long.county == "Shannon")]
    assert set(shannon.county_geoid) == {"0500000US46113"}  # Shannon until 2015
    assert "Shannon" not in set(simple.county) and "Washabaugh" not in set(simple.county)
    assert set(simple.party) == {"republican", "democratic", "other"}
    assert long[long.date == "1978-10-23"].election.iloc[0] == "general"
    assert chart["dates"][-1] == long.date.max() and len(chart["counties"]) == 66
    i = chart["dates"].index("2026-10-02")
    assert chart["sources"][i].endswith("StatewideVotersByCounty_10.2.2026.pdf")
    total = simple[(simple.date == "2026-10-02") & (simple.party == "republican")].voters.sum()
    assert chart["state"]["republican"][i] == total
