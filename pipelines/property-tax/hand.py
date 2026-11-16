"""Statistical report rows settled by hand (the app's review pages; sddocs.review): report
lines that weren't read, and municipal rows whose total rates don't add up to their levies.

Each is one row, keyed by its kind, year, page and the line's name and school district:
"school 2018 p45 GREGORY 26-4", "municipality 2020 p16 Hot Springs Fall River 23-3"; with
" #2", " #3"… for later lines on the page with the same name and district. A
decision sets a cell (by column name) of that row; on a line that wasn't read, decisions
make a row ("by hand"); a decision on cell "*" says the line has no row to read. Decisions
are kept in fixes.toml and applied on every build.
"""

from __future__ import annotations

import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
BY_HAND = HERE / "fixes.toml"
SCHOOL_COLUMNS = [("county", "text"), ("district", "text"), ("district_number", "text"),
                  ("value_agricultural", "money"), ("value_owner_occupied", "money"),
                  ("value_other_non_ag", "money"), ("value_centrally_assessed", "money"),
                  ("levy_agricultural", "number"), ("levy_owner_occupied", "number"),
                  ("levy_other", "number"), ("taxes", "money")]  # fmt: skip
NAME = re.compile(r"^(?P<name>.+?) (?P<sd>\d{1,2}-\d{1,2})(?= )")


def key_of_line(u: dict) -> str | None:
    """An unread line's key (None: a line that isn't a school or municipal row)."""
    m = NAME.match(u["line"])
    if not m or u.get("table") not in ("schools", "municipalities"):
        return None
    kind = "school" if u["table"] == "schools" else "municipality"
    n = u.get("_n", 1)
    return f"{kind} {u['file'][:4]} p{u['page']} {m['name']} {m['sd']}" + (f" #{n}" if n > 1
                                                                           else "")  # fmt: skip


def key_of_municipality(r: dict) -> str:
    n = r.get("_n", 1)
    return (f"municipality {r['file'][:4]} p{r['page']} {r['municipality']} {r['county']} "
            f"{r['school_district_number']}" + (f" #{n}" if n > 1 else ""))  # fmt: skip


def municipal_columns(columns: list[str]) -> list[tuple[str, str]]:
    return [("municipality", "text"), ("county", "text"), ("school_district_number", "text"),
            ("population", "int"), ("valuation", "money"),
            *((c, "number") for c in columns), ("taxes_paid", "money")]  # fmt: skip


def prefill(u: dict) -> dict:
    """What an unread line gives for sure: its name, school district and county."""
    import report

    m = NAME.match(u["line"])
    if u["table"] == "schools":
        return {"county": u.get("county"), "district": m["name"], "district_number": m["sd"]}
    place = report._split_place(m["name"])
    return {"municipality": place[0] if place else m["name"],
            "county": place[1] if place else None, "school_district_number": m["sd"]}  # fmt: skip


def decisions() -> dict[str, dict]:
    """Decisions by key: {cell: decision}."""
    from sddocs import review

    out: dict[str, dict] = {}
    for (key, cell), d in review.load(BY_HAND).items():
        out.setdefault(key, {})[cell] = d
    return out


def _reasons(decided: dict) -> str:
    return "; ".join(f"{cell}: {d['reason']}" for cell, d in decided.items())


def row_from_line(u: dict, decided: dict) -> dict | None:
    """The row decisions make of an unread line (None: decided to have no row)."""
    if "*" in decided:
        return None
    row = {"payable_year": u.get("payable_year"), **prefill(u),
           **{cell: d.get("value") for cell, d in decided.items()},
           "file": u["file"], "page": u["page"], "read_from": "by hand",
           "by_hand": _reasons(decided)}  # fmt: skip
    if u["table"] == "municipalities":
        import report

        row["rates_check"] = report.rates_check(row)
    return row


def settle_municipality(r: dict, decided: dict) -> None:
    for cell, d in decided.items():
        if cell != "*":
            r[cell] = d.get("value")
    r["by_hand"] = _reasons(decided)


# --- the review queue --------------------------------------------------------------------


def _page_image(u: dict, scanned: set) -> dict:
    import ocr

    year, page = int(u["file"][:4]), u["page"]
    image = {"pdf": f".cache/property-tax/raw/statistical-reports/{u['file']}",
             "pages": [page - 1]}  # fmt: skip
    if page in scanned and (stored := ocr.stored(year, page)) is not None:
        image["rotation"] = stored.get("rotation", 0)
        if (box := ocr.line_box(stored, u["line"])) is not None:
            image["box"] = box
    return image


def item_for_line(u: dict, scanned: set, url: str):
    from sddocs import review

    columns = (SCHOOL_COLUMNS if u["table"] == "schools"
               else municipal_columns(u["columns"]))  # fmt: skip
    values = prefill(u)
    key = key_of_line(u)
    return review.Item(
        source="property-tax", key=key, kind="table",
        title=f"{values.get('district') or values.get('municipality')} "
              f"({values.get('county') or 'county not read'}), payable {u.get('payable_year')}",
        subtitle=f"{u['file'][:4]} statistical report, page {u['page']}: a line not read",
        links=[{"label": "the report", "url": url}], image=_page_image(u, scanned),
        problems=[f"The line as read: {u['line']}",
                  "Its figures don't fit the table's columns; enter the row from the page "
                  "(or save with nothing entered if the line has no row to read)."],
        sections=[_section(key, "School district" if u["table"] == "schools" else
                           "Municipality", columns, values, {})],
        context={"kind": u["table"]})  # fmt: skip


def item_for_municipality(r: dict, columns: list[str], scanned: set, line: str | None):
    from sddocs import review

    key = key_of_municipality(r)
    u = {"file": r["file"], "page": r["page"], "line": line or ""}
    return review.Item(
        source="property-tax", key=key, kind="table",
        title=f"{r['municipality']} ({r['county']}), payable {r['payable_year']}",
        subtitle=f"{r['file'][:4]} statistical report, page {r['page']}: rates don't add up",
        links=[{"label": "the report", "url": r.get("source_url", "")}],
        image=_page_image(u, scanned),
        problems=["Its total rates aren't the city's, the county's and the school's levies "
                  "(nor those and the same other levy)."],
        sections=[_section(key, "Municipality", municipal_columns(columns), r, r)],
        context={"kind": "municipalities"})  # fmt: skip


def _section(key: str, name: str, columns: list[tuple[str, str]], values: dict,
             read: dict) -> dict:  # fmt: skip
    return {"name": name,
            "columns": [{"key": c, "label": c.replace("_", " ").capitalize(), "kind": k}
                        for c, k in columns],
            "rows": [{"name": "", "cells": {c: {"field": c, "value": values.get(c),
                                                "readings": ({"read": [read[c]]}
                                                             if read.get(c) is not None
                                                             else {})}
                                            for c, _ in columns}}]}  # fmt: skip


def problems(key: str, cells: dict) -> list[str]:
    """A row's problems with these values (the review page's check)."""
    import report

    if key.startswith("municipality"):
        out = []
        check = report.rates_check(cells)
        if check not in ("adds up",) and not check.startswith("another levy"):
            out.append(f"total rates and levies: {check or 'a levy is blank'}")
        if not report._taxes_fit(cells, cells.get("taxes_paid")):
            out.append("the taxes paid don't fit the valuation at these rates")
        return out
    f = {k: (v or 0) for k, v in cells.items() if not isinstance(v, str)}
    other = f.get("value_other_non_ag", 0) + f.get("value_centrally_assessed", 0)
    computed = (f.get("levy_agricultural", 0) * f.get("value_agricultural", 0)
                + f.get("levy_owner_occupied", 0) * f.get("value_owner_occupied", 0)
                + f.get("levy_other", 0) * other) / 1000  # fmt: skip
    taxes = f.get("taxes", 0)
    if abs(computed - taxes) > abs(taxes) * 0.001 + 5:
        return [
            f"taxes {taxes:,.2f}; the valuations × levies are {computed:,.2f} (many "
            "reports' school taxes run a little above, so this can be as printed)"
        ]
    return []
