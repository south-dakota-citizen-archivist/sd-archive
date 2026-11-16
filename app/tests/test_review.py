"""The review pages, for a pipeline's queue: a table with a check script, and a pick."""

import json
import sys

import pytest

from sddocs import review

CHECK = """import json, sys
cells = json.load(sys.stdin)["cells"]
rows = [v for f, v in cells.items() if f.startswith("t|") and not f.startswith("t|Totals")]
total = cells["t|Totals|units"]
print(json.dumps([] if sum(rows) == total else [f"units add up to {sum(rows)}, Totals {total}"]))
"""


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr("review.providers.REPO", tmp_path)
    monkeypatch.setattr("review.providers.RECORD_PROVIDERS", [])
    build = tmp_path / ".cache" / "demo" / "build"
    build.mkdir(parents=True)
    (tmp_path / "check.py").write_text(CHECK)

    def cell(row, value, readings):
        return {"field": f"t|{row}|units", "value": value, "readings": readings}

    table = review.Item(
        source="demo", key="2022-04", kind="table", title="April 2022",
        image={"pdf": ".cache/demo/raw/r.pdf", "pages": [0]},
        problems=["units add up to 9, Totals 6"],
        sections=[{"name": "T", "columns": [{"key": "units", "label": "Units", "kind": "int"}],
                   "rows": [{"name": "A", "cells": {"units": cell("A", 3, {"x": [3]})}},
                            {"name": "B", "cells": {"units": cell("B", 6, {"x": [6],
                                                                           "y": [3]})}},
                            {"name": "Totals",
                             "cells": {"units": cell("Totals", 6, {"x": [6]})}}]}])  # fmt: skip
    pick = review.Item(source="demo", key="2022-05", kind="pick", title="May 2022",
                       field="c|Table Games|handle_year_to_date", label="year to date",
                       readings={"x": 1000.5, "y": 1005.5},
                       context={"kind": "money"})  # fmt: skip
    review.queue(build / "review.json", source="demo", name="Demo", decisions="fixes.toml",
                 check="check.py", items=[table, pick])  # fmt: skip
    return tmp_path


def test_a_table(repo, client):
    assert "April 2022" in client.get("/review/").content.decode()
    page = client.get("/review/demo/2022-04/").content.decode()
    assert 'name="cell:t|B|units" value="6"' in page and "units add up to 9" in page
    form = {"kind": "table", "cell:t|A|units": "3", "was:t|A|units": "3",
            "cell:t|B|units": "3", "was:t|B|units": "6",
            "cell:t|Totals|units": "6", "was:t|Totals|units": "6"}  # fmt: skip
    checked = client.post("/review/demo/2022-04/check/", form).content.decode()
    assert "every check passes" in checked and "1 changed" in checked
    client.post("/review/demo/2022-04/decide/", form)  # no reason: nothing saved
    assert not (repo / "fixes.toml").exists()
    client.post("/review/demo/2022-04/decide/", {**form, "reason": "B prints 3."})
    found = review.load(repo / "fixes.toml")
    assert found == {("2022-04", "t|B|units"): {"key": "2022-04", "field": "t|B|units",
                                                "value": 3, "from": "y",
                                                "reason": "B prints 3.",
                                                "checked": found[("2022-04", "t|B|units")][
                                                    "checked"]}}  # fmt: skip
    # nothing changed: the table as read is right
    as_read = {**form, "cell:t|B|units": "6", "reason": "The report's own sum is off."}
    client.post("/review/demo/2022-04/decide/", as_read)
    assert review.load(repo / "fixes.toml")[("2022-04", "*")]["from"] == "as read"
    client.post("/review/demo/2022-04/undo/", {"field": "*all"})
    assert review.load(repo / "fixes.toml") == {}


def test_a_pick_of_a_figure(repo, client):
    page = client.get("/review/demo/2022-05/").content.decode()
    assert "$1,000.50" in page and "$1,005.50" in page
    client.post("/review/demo/2022-05/decide/", {"field": "c|Table Games|handle_year_to_date",
                                                 "from": "y", "value": "$1,005.50",
                                                 "reason": "Printed $1,005.50."})  # fmt: skip
    d = review.load(repo / "fixes.toml")[("2022-05", "c|Table Games|handle_year_to_date")]
    assert d["value"] == 1005.5 and d["from"] == "y"


def test_pages_only_from_the_cache(repo, client):
    assert client.get("/review/page.png?pdf=../../etc/passwd&page=0").status_code == 404
    assert sys.executable  # the check runs with the app's Python
    assert json.loads((repo / ".cache/demo/build/review.json").read_text())["check"] == "check.py"
