"""Reading the monthly summaries that are scans: the yearly files for 2018–2022 (each month's
summary, one or two pages, scanned and bound together) and the few scanned months since.

A scan is read by three OCR engines (rapidocr; Apple Vision through `textra` and Tesseract,
where installed), and each engine's pieces are grouped into lines at a few tolerances, since rows
printed close together can run into each other or split apart. That makes several readings
of a month. The first that passes every check (checks.report_checks) is used.

When none does, the readings are combined: each row, Totals line, handle comparison line and
tax line is taken from the first reading that has it, and where readings differ on one, the
others' versions are tried, one or two at a time, until every check passes. The checks tie
nearly every figure to others (rows to their Totals, each payout to its handle and win, the
tax lines to the taxable revenue and the 9%), so a figure taken from another reading is
confirmed by the arithmetic, not chosen for it. The month records what came from where. A
month no combination passes is left out.

OCR pieces are kept under .cache/gaming/ocr/ so a parser change doesn't mean reading again.
"""

from __future__ import annotations

import itertools
import json
import re
from pathlib import Path

import parse
from checks import report_checks  # (at import: another pipeline has a checks.py too)

HERE = Path(__file__).resolve().parent
OCR_CACHE = HERE.parents[1] / ".cache" / "gaming" / "ocr"
ENGINES = {"rapidocr": "rapidocr", "textra": "Apple Vision", "tesseract": "Tesseract"}
TOLERANCES = (5, 3, 8)
FOOTER = re.compile(r"(January|February|March|April|May|June|July|August|September|October|"
                    r"November|December) (\d{4}) South Dakota Gaming Statistics")  # fmt: skip
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December")  # fmt: skip
MAX_SWAPS = 2


def months_in(pdf: Path) -> dict[str, list[int]]:
    """The pages (0-based) of each month's summary in a yearly file, by the footer every page
    carries as text ("January 2018 South Dakota Gaming Statistics 1")."""
    import pdfplumber

    out: dict[str, list[int]] = {}
    with pdfplumber.open(pdf) as doc:
        for i, page in enumerate(doc.pages):
            if m := FOOTER.search(page.extract_text() or ""):
                out.setdefault(f"{m[2]}-{MONTHS.index(m[1]) + 1:02d}", []).append(i)
    return out


def _digest(pdf: Path) -> str:
    import hashlib

    return hashlib.sha256(pdf.read_bytes()).hexdigest()[:16]


def pieces(pdf: Path, index: int, engine: str) -> list[dict]:
    """One page's OCR pieces, read once and kept (by the file's contents)."""
    from sddocs import ocr

    path = OCR_CACHE / _digest(pdf) / f"{index}.{engine}.json"
    if path.exists():
        return json.loads(path.read_text())
    found = ocr.page_pieces(pdf, index, engine)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(found))
    return found


def engines() -> list[str]:
    from sddocs import ocr

    return [e for e in ENGINES if ocr.available(e)]


def readings(pdf: Path, pages: list[int], get=None) -> list[tuple[str, list[list[str]]]]:
    """Every reading of a month's pages. `get(pdf, index, engine)`: a page's OCR pieces
    (pieces(), unless given)."""
    out = []
    for engine in engines():
        found = [(get or pieces)(pdf, i, engine) for i in pages]
        for tolerance in TOLERANCES:
            out.append((f"{ENGINES[engine]}, lines within {tolerance} pt",
                        [parse.ocr_lines(p, tolerance) for p in found]))  # fmt: skip
        by_column = column_lines(found[0], out[-len(TOLERANCES)][1][0])
        if by_column:
            out.append((f"{ENGINES[engine]}, by column", [by_column, *out[-1][1][1:]]))
    return out


SINGLE = {"units": re.compile(r"^\d+$"), "money": re.compile(rf"^{parse.MONEY}$"),
          "payout": re.compile(rf"^(?:{parse.PCT})$")}  # fmt: skip


def _column_rows(region: list[dict], known: tuple[str, ...]) -> list[str] | None:
    """A table's rows from its cells read column by column: some reports print a column a
    little above or below the others, so a row's cells aren't on one line, but each column
    is in order top to bottom. None unless every cell is one figure or a name and every
    column has as many as the names."""
    mid = lambda p: (p["top"] + p["bottom"]) / 2  # noqa: E731
    names = [p for p in region if parse._name(p["text"].strip(), known) in known]
    if not names:
        return None
    region = [p for p in region if mid(p) >= min(mid(n) for n in names) - 4]  # no headers
    columns: dict[str, list[dict]] = {"units": [], "money": [], "payout": []}
    for p in region:
        if p in names:
            continue
        kind = next((k for k, rx in SINGLE.items() if rx.match(p["text"].strip())), None)
        if kind is None:
            return None
        columns[kind].append(p)
    money = sorted(columns.pop("money"), key=lambda p: p["x0"])
    if len(money) < 2:
        return None
    gap = max(range(1, len(money)), key=lambda i: money[i]["x0"] - money[i - 1]["x0"])
    columns["handle"], columns["win"] = money[:gap], money[gap:]
    n = len(names)
    if any(len(c) != n for c in columns.values()):
        return None
    order = {k: sorted(v, key=mid) for k, v in
             {"names": names, **columns}.items()}  # fmt: skip
    return [" ".join(parse._name(order["names"][i]["text"].strip(), known) if k == "names"
                     else order[k][i]["text"].strip()
                     for k in ("names", "units", "handle", "win", "payout"))
            for i in range(n)]  # fmt: skip


def column_lines(found: list[dict], lines: list[str]) -> list[str] | None:
    """Page 1's lines with the slot machine and table game rows read by column (rapidocr's
    and Vision's pieces are cells; Tesseract's are lines, so its readings have none)."""

    def first(text):
        return next((p for p in found if p["text"].strip() == text), None)

    slots, games = first("Slot Machines"), first("Table Games")
    after = [
        p
        for p in found
        if games
        and p["top"] > games["top"] + 5
        and (p["text"].strip() == "Handle Comparison" or "Sports Wagering" in p["text"])
    ]
    if not (slots and games and after):
        return None
    end = min(p["top"] for p in after)
    rows = {}
    for name, top, bottom, known in (
        ("Slot Machines", slots["bottom"], games["top"], parse.SLOT_NAMES),
        ("Table Games", games["bottom"], end, parse.GAME_NAMES),
    ):
        region = [p for p in found if top <= (p["top"] + p["bottom"]) / 2 < bottom]
        rows[name] = _column_rows(region, known)
        if rows[name] is None:
            return None
    try:
        a, b = lines.index("Slot Machines"), lines.index("Table Games")
        c = next(
            i
            for i in range(b + 1, len(lines))
            if lines[i] == "Handle Comparison" or parse._heading(lines[i], "Sports Wagering")
        )
    except (ValueError, StopIteration):
        return None
    return [
        *lines[: a + 1],
        *rows["Slot Machines"],
        "Table Games",
        *rows["Table Games"],
        *lines[c:],
    ]


# --- combining readings ----------------------------------------------------------------


# the handle comparison's figures no check within a month covers: each is kept from a scan
# only where two engines read it alike (build checks them again against other months)
UNCHECKED = ("change_from_a_year_before", "handle_year_to_date", "year_to_date_change")
KNOWN = {"slots": parse.SLOT_NAMES, "table_games": parse.GAME_NAMES}


def _parts(r: dict) -> dict[tuple, object]:
    out: dict[tuple, object] = {}
    for kind in ("slots", "table_games", "sports"):
        for row in r[kind]:
            if kind in KNOWN and row["name"] not in KNOWN[kind]:
                continue  # a misread name ("26 Dollars"): the rows that pass are all known
            out.setdefault((kind, row["name"]), row)
    for key in ("slots_total", "table_games_total", "sports_wagering_total",
                "sports_detail_total"):  # fmt: skip
        if r[key]:
            out[(key,)] = r[key]
    for name, line in r["handle_comparison"].items():
        for field, value in line.items():
            out[("handle_comparison", name, field)] = value
    for key, value in r["tax"].items():
        out[("tax", key)] = value
    return out


def _report(month: str, parts: dict[tuple, object]) -> dict:
    r = {"month": month, "handle_comparison": {}, "tax": {},
         **{k: [] for k in ("slots", "table_games", "sports")},
         **{k: None for k in ("slots_total", "table_games_total", "sports_wagering_total",
                              "sports_detail_total")}}  # fmt: skip
    order = {**{("slots", n): i for i, n in enumerate(parse.SLOT_NAMES)},
             **{("table_games", n): i for i, n in enumerate(parse.GAME_NAMES)}}  # fmt: skip
    for key in sorted(parts, key=lambda k: order.get(k, 99)):
        value = parts[key]
        if len(key) == 1:
            r[key[0]] = value
        elif key[0] == "handle_comparison":
            r["handle_comparison"].setdefault(key[1], {})[key[2]] = value
        elif key[0] == "tax":
            r[key[0]][key[1]] = value
        else:
            r[key[0]].append(value)
    return r


def problems(r: dict) -> list[str]:
    """What keeps a report from being used: a part missing, or a check failing."""

    missing = [k for k in ("slots_total", "table_games_total") if not r[k]]
    required = ("total_statistical_win", "taxable_adjusted_gross_revenue", "tax_due",
                "tax_collected")  # fmt: skip
    missing += [f"tax {k}" for k in required if r["tax"].get(k) is None]
    sections = 4 if r["sports_wagering_total"] else 3
    if len(r["handle_comparison"]) != sections:
        missing.append(f"handle comparison: {sorted(r['handle_comparison'])}")
    if r["sports_wagering_total"] and not r["sports_detail_total"] and r["sports"]:
        missing.append("sports detail Totals")
    for kind in ("slots", "table_games", "sports"):
        for row in [*r[kind], r[f"{kind}_total"] if kind != "sports" else r["sports_detail_total"]]:
            for col in ("units", "handle", "statistical_win"):
                if row and col in COLUMNS[kind] and row.get(col) is None:
                    missing.append(f"{kind.replace('_', ' ')} {row['name']} {col}")
    for name, line in r["handle_comparison"].items():
        if line.get("handle") is None:
            missing.append(f"handle comparison {name} handle")
    if missing:
        return [f"missing: {m}" for m in missing]
    try:
        return report_checks(r)
    except (KeyError, TypeError, ZeroDivisionError) as e:
        return [f"check failed to run: {e!r}"]


# --- cells: a report's figures by name, for decisions and the review page ---------------

SEP = "|"  # "slots|1 Cents|units"; a sport's name can have a slash ("MMA/UFC")
COLUMNS = {"slots": ("units", "handle", "statistical_win", "payout_percent"),
           "table_games": ("units", "handle", "statistical_win", "payout_percent"),
           "sports": ("handle", "statistical_win", "payout_percent")}  # fmt: skip
SPORTS_TOTAL = ("casinos_reporting", "handle", "statistical_win", "payout_percent")
COMPARISON = ("handle", "change_from_a_year_before", "handle_year_to_date", "year_to_date_change")
KINDS = {"units": "int", "casinos_reporting": "int", "handle": "money",
         "statistical_win": "money", "payout_percent": "pct", "handle_year_to_date": "money",
         "change_from_a_year_before": "pct", "year_to_date_change": "pct",
         "amount": "money"}  # fmt: skip
TOTALS = {"slots": "slots_total", "table_games": "table_games_total",
          "sports": "sports_detail_total"}  # fmt: skip


def cells(r: dict) -> dict[str, object]:
    """Every figure in a report by its cell name."""
    out = {}
    for kind, cols in COLUMNS.items():
        for row in [*r[kind], *([r[TOTALS[kind]]] if r[TOTALS[kind]] else [])]:
            for col in cols:
                out[SEP.join((kind, row["name"], col))] = row.get(col)
    if r["sports_wagering_total"]:
        for col in SPORTS_TOTAL:
            out[SEP.join(("sports_wagering", "Totals", col))] = r["sports_wagering_total"][col]
    for name, line in r["handle_comparison"].items():
        for col in COMPARISON:
            out[SEP.join(("handle_comparison", name, col))] = line.get(col)
    for key, value in r["tax"].items():
        out[SEP.join(("tax", key, "amount"))] = value
    return out


def set_cell(r: dict, field: str, value) -> None:
    kind, name, col = field.split(SEP)
    if kind == "tax":
        r["tax"][name] = value
    elif kind == "handle_comparison":
        r["handle_comparison"].setdefault(name, {})[col] = value
    elif kind == "sports_wagering":
        r["sports_wagering_total"] = r["sports_wagering_total"] or {"name": "Totals"}
        r["sports_wagering_total"][col] = value
    elif name == "Totals":
        r[TOTALS[kind]] = {**(r[TOTALS[kind]] or {"name": "Totals"}), col: value}
    else:
        rows = r[kind]
        row = next((x for x in rows if x["name"] == name), None)
        if row is None:
            row = {"name": name}
            rows.append(row)
            known = KNOWN.get(kind)
            if known:
                rows.sort(key=lambda x: known.index(x["name"]) if x["name"] in known else 99)
        row[col] = value


def from_cells(month: str, values: dict[str, object]) -> dict:
    """A report from cell values (the review page's table); a row with no figures is left
    out."""
    r = _report(month, {})
    for field, value in values.items():
        set_cell(r, field, value)
    for kind in COLUMNS:
        r[kind] = [x for x in r[kind] if any(x.get(c) is not None for c in COLUMNS[kind])]
    return r


def _hand(r: dict, hand: dict[str, object]) -> dict:
    """A report with the cells decided by hand set (a copy)."""
    import copy

    r = copy.deepcopy(r)
    for field, value in hand.items():
        set_cell(r, field, value)
    return r


def _describe(key: tuple) -> str:
    return " ".join(str(k).replace("_", " ") for k in key)


def _engine(label: str) -> str:
    return label.split(",")[0]


def _agreed(key: tuple, parsed: list[tuple[str, dict]]) -> tuple[bool, object]:
    """(whether two engines read the figure alike, and the figure: None for "N/A")."""
    by_engine: dict[str, set] = {}
    for label, parts in parsed:
        if key in parts:  # None: printed "N/A"
            by_engine.setdefault(_engine(label), set()).add(parts[key])
    counts: dict = {}
    for values in by_engine.values():
        for v in values:
            counts[v] = counts.get(v, 0) + 1
    agreed = [v for v, n in counts.items() if n >= 2]
    return (True, agreed[0]) if len(agreed) == 1 else (False, None)


def _candidates(parsed: list[tuple[str, dict]]) -> dict[tuple, list[tuple[str, object]]]:
    candidates: dict[tuple, list[tuple[str, object]]] = {}
    for label, parts in parsed:
        for key, value in parts.items():
            seen = candidates.setdefault(key, [])
            if all(value != v for _, v in seen):
                seen.append((label, value))
    return candidates


def _combine(month: str, parsed: list[tuple[str, dict]],
             hand: dict[str, object]) -> tuple[dict | None, str]:  # fmt: skip
    """Each part from the first reading that has it; where readings differ on a part, the
    others' versions, up to MAX_SWAPS at a time, until every check passes. Cells decided
    by hand are set in every combination."""
    candidates = _candidates(parsed)
    base = {k: v[0][1] for k, v in candidates.items()}
    differing = [k for k, v in candidates.items() if len(v) > 1 and k[-1] not in UNCHECKED]
    for n in range(MAX_SWAPS + 1):
        for keys in itertools.combinations(differing, n):
            for picks in itertools.product(*(candidates[k][1:] for k in keys)):
                swapped = {k: v for k, (_, v) in zip(keys, picks, strict=True)}
                r = _hand(_report(month, {**base, **swapped}), hand)
                if not problems(r):
                    how = "combined: " + parsed[0][0]
                    if keys:
                        how += "; " + "; ".join(f"{_describe(k)} from {label}"
                                                for k, (label, _) in zip(keys, picks,
                                                                         strict=True))  # fmt: skip
                    return r, how
    return None, ""


def cell_readings(parsed: list[tuple[str, dict]]) -> dict[str, dict[str, list]]:
    """Each cell's values in each engine's readings: {cell: {engine: [values]}}."""
    out: dict[str, dict[str, list]] = {}
    for label, parts in parsed:
        for field, value in cells(_report("", parts)).items():
            seen = out.setdefault(field, {}).setdefault(_engine(label), [])
            if value not in seen:
                seen.append(value)
    return out


def read_scan(pdf: Path, pages: list[int], month: str, get=None, decided: dict | None = None,
              attempt: dict | None = None) -> tuple[dict | None, str, list[str]]:  # fmt: skip
    """A scanned month: the report, how it was read, and what doesn't add up (when it's left
    out, None and why).

    `decided`: decisions from the review pages for this month ({cell: decision}; cell "*":
    the table as read is right). A cell decided by hand is set in every reading; a month with
    decisions is used even when its checks still fail (they're returned, to be noted).
    `attempt` (a dict) is filled with the closest the readings come, for the review queue:
    "report", "readings" (cell_readings) and "problems"."""
    decided = decided or {}
    hand = {f: d.get("value") for f, d in decided.items() if f != "*"}
    parsed, first_problems, chosen = [], None, None
    for label, found in readings(pdf, pages, get):
        try:
            r = parse.read_lines(found, month=month, strict=False)
        except parse.ReportProblem as e:
            first_problems = first_problems or [str(e)]
            continue
        p = problems(_hand(r, hand))
        if not p and chosen is None:
            chosen = (_hand(r, hand), label)
        first_problems = first_problems or p
        parsed.append((label, _parts(r), len(p)))
    # combining starts from the readings with the fewest problems
    parsed = [(label, parts) for label, parts, _ in sorted(parsed, key=lambda x: x[2])]
    if chosen is None and parsed:
        chosen = _combine(month, parsed, hand)
    left = []
    if (not chosen or chosen[0] is None) and parsed:
        closest = _hand(_report(month, {k: v[0][1] for k, v in _candidates(parsed).items()}),
                        hand)  # fmt: skip
        left = problems(closest)
        if attempt is not None:
            attempt.update(report=closest, readings=cell_readings(parsed), problems=left)
        if decided:  # settled by hand: used, with what still doesn't add up noted
            chosen = (closest, "closest reading")
    if not chosen or chosen[0] is None:
        return None, "ocr", left or first_problems or ["no reading"]
    r, how = chosen
    # the figures no check within the month covers: kept where two engines read them alike;
    # otherwise every engine's version is kept for build to confirm against other months
    r["unconfirmed"] = {}
    for name, line in r["handle_comparison"].items():
        for field in UNCHECKED:
            key = ("handle_comparison", name, field)
            cell = SEP.join(("handle_comparison", name, field))
            if cell in hand:
                line[field] = hand[cell]
                continue
            found, line[field] = _agreed(key, parsed)
            if not found:
                by_engine: dict[str, list] = {}
                for label, parts in parsed:
                    if key in parts and parts[key] not in by_engine.get(_engine(label), []):
                        by_engine.setdefault(_engine(label), []).append(parts[key])
                r["unconfirmed"][(name, field)] = by_engine
    if decided:
        r["by_hand"] = {f: d["reason"] for f, d in decided.items()}
        how += f"; by hand: {', '.join(sorted(decided))}"
    return r, f"ocr ({how})", left


def table_item(month: str, title: str, subtitle: str, pdf: str, pages: list[int], url: str,
               attempt: dict):  # fmt: skip
    """A left-out month for the review page: every cell with the closest reading's value and
    each engine's, sections as the report prints them."""
    from sddocs import review

    r, readings_ = attempt["report"], attempt["readings"]

    def cell(field):
        return {"field": field, "value": cells(r).get(field), "readings": readings_.get(field, {})}

    def names(kind, known=None):
        found = {f.split(SEP)[1] for f in readings_ if f.startswith(kind + SEP)}
        found |= {x["name"] for x in r[kind]}
        found.discard("Totals")
        if known:  # every row the report prints, read or not (keno: when a reading has it)
            return [n for n in known if n != "Totals" and (n != "Keno" or n in found)]
        return sorted(found)

    def section(name, kind, rows, cols):
        return {"name": name, "columns": [{"key": c, "label": c.replace("_", " ").capitalize(),
                                           "kind": KINDS[c]} for c in cols],
                "rows": [{"name": row, "cells": {c: cell(SEP.join((kind, row, c)))
                                                 for c in cols}} for row in rows]}  # fmt: skip

    sections = [
        section("Slot machines", "slots", [*names("slots", parse.SLOT_NAMES), "Totals"],
                COLUMNS["slots"]),
        section("Table games", "table_games",
                [*names("table_games", parse.GAME_NAMES), "Totals"], COLUMNS["table_games"]),
    ]  # fmt: skip
    if month >= "2021-09":
        sections.append(section("Sports wagering", "sports_wagering", ["Totals"], SPORTS_TOTAL))
        sections.append(section("Sports wagering by sport", "sports",
                                [*names("sports"), "Totals"], COLUMNS["sports"]))  # fmt: skip
    comparison = ["Slot Machines", "Table Games",
                  *(["Sports Wagering"] if month >= "2021-09" else []), "Total Handle"]  # fmt: skip
    sections.append(section("Handle comparison", "handle_comparison", comparison, COMPARISON))
    tax = list(dict.fromkeys([*parse.TAX_LINES.values()]))
    present = {f.split(SEP)[1] for f in readings_ if f.startswith("tax" + SEP)} | set(r["tax"])
    sections.append(section("Tax", "tax", [t for t in tax if t in present], ("amount",)))
    return review.Item(source="gaming", key=month, kind="table", title=title, subtitle=subtitle,
                       links=[{"label": "the report", "url": url}],
                       image={"pdf": pdf, "pages": pages}, problems=attempt["problems"],
                       sections=sections)  # fmt: skip
