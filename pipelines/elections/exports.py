"""Read the site's export spreadsheets (ResultsExport.aspx), which capture.py saves raw.

Each county's workbook has a sheet per race on that county's ballot, named by the race ID
and the start of its name ("4265Presidential Electors"). On each sheet, after a header block
(election, "Precincts 735 of 735 Reporting", "Aurora County", "Downloaded at …"):

    <race name>\\n<district or county>   Precinct      <candidate>   <candidate> …
                                         Precinct-01   430           286
                                         …
                                         TOTALS        2,323         …

Primaries' sheet names start with the ballot's party ("REP14452Governor"; "NON" for the
nonpartisan ballot), kept as party_ballot. The 2020 primary's sheets have the party and name
but no race ID ("REPState Rep District 06"), so they can't be matched to races and are skipped.

2012's exports have the votes for each precinct but leave the precinct names blank; those
rows get precinct "" and their order on the sheet (precinct_seq). Sheets whose name ends in
"  RECOUNT" (or the start of it, cut off at 31 characters) are recounts, next to the race's
original sheet. They're marked recount=True, and build.py puts them in their own table.

Some workbooks repeat a sheet, with "_1" added to its name. A repeat with the same rows is
dropped; one with different rows is kept and listed in `different_repeats`.

The statewide and legislative workbooks are by county, so their rows aren't read: the county
workbooks have the same races in more detail. Their sheet names are read, though, because
they say which races are statewide or legislative (`race_types`).
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

SKIP = {"Statewide-Export", "Legislative-Export"}
# Recount sheets end in "  RECOUNT" (" - RECOUNT" in the legislative workbook), cut short by
# Excel's 31-character limit: "…  RE", "…  RECO". A name cut off mid-word ("A RESOLUTION RE")
# has one space, so isn't a recount.
# Primaries put the ballot's party first: "REP14452Governor", "NON13017City Council"
SHEET = re.compile(
    r"^(?P<party>[A-Z]{3}(?=\d))?(?P<race>\d+)(?P<name>.*?)"
    r"(?P<recount>(?:\s{2}|\s-\s)RE(?:C|CO|COU|COUN|COUNT)?)?$"
)
REPEAT = re.compile(r"_\d+$")
BALLOT_QUESTION = re.compile(
    r"^[A-Z]{0,3}\d+(Constitutional|Initiated|Referred|Amendment|Referendum)"
)


def _number(value) -> int | None:
    s = str(value).replace(",", "").strip()
    return int(float(s)) if re.fullmatch(r"-?\d+(\.0+)?", s) else None


def read_sheet(frame: pd.DataFrame) -> list[dict]:
    """Rows of one sheet: precinct, candidate, votes; plus the TOTALS row (precinct=None)."""
    rows = []
    cells = frame.astype(object).where(frame.notna(), "")
    header = [i for i in range(len(cells)) if str(cells.iat[i, 1]).strip() == "Precinct"]
    for h in header:
        title = str(cells.iat[h, 0]).strip()
        race, _, subtitle = title.partition("\n")
        candidates = {
            c: " ".join(str(cells.iat[h, c]).split())
            for c in range(2, cells.shape[1])
            if str(cells.iat[h, c]).strip()
        }
        seq = 0
        for i in range(h + 1, len(cells)):
            label = " ".join(str(cells.iat[i, 1]).split())
            values = {c: _number(cells.iat[i, c]) for c in candidates}
            if label == "Precinct" or (not label and all(v is None for v in values.values())):
                break
            total = label == "TOTALS"
            seq += not total
            for c, name in candidates.items():
                rows.append(
                    {
                        "race_title": " ".join(race.split()),
                        "race_subtitle": " ".join(subtitle.split()),
                        "precinct": None if total else label,
                        "precinct_seq": None if total else seq,
                        "candidate": name,
                        "votes": values[c],
                    }
                )
            if total:
                break
    return rows


def read_workbook(path: Path, different_repeats: list | None = None) -> list[dict]:
    """Every row of one county's workbook, with its race ID, sheet and county."""
    out = []
    sheets = pd.read_excel(path, sheet_name=None, header=None, dtype=str)
    for sheet, frame in sheets.items():
        original = REPEAT.sub("", sheet)
        if original != sheet and original in sheets:
            if frame.equals(sheets[original]):
                continue
            if different_repeats is not None:
                different_repeats.append((path.name, sheet))
        m = SHEET.match(original)
        if not m or frame.shape[1] < 3:
            continue
        county = ""
        for i in range(min(6, len(frame))):
            cell = str(frame.iat[i, 0])
            if cell.endswith(" County"):
                county = cell.removesuffix(" County").strip()
        for r in read_sheet(frame):
            out.append(
                {
                    "race_id": m["race"],
                    "party_ballot": m["party"] or "",
                    "recount": bool(m["recount"]),
                    "county": county,
                    "file": path.name,
                    "sheet": sheet,
                    **r,
                }
            )
    return out


def read_election(root: Path, different_repeats: list | None = None) -> list[dict]:
    """Every county workbook captured for one election (root = raw/<eid>)."""
    out = []
    for path in sorted((root / "exports").glob("*.xlsx")):
        if path.stem not in SKIP:
            out += read_workbook(path, different_repeats)
    return out


def race_types(root: Path) -> dict[str, str]:
    """Race ID → type (LEG, SWR or BQ), from the sheet names of the summary workbooks."""
    out = {}
    for stem, kind in (("Legislative-Export", "LEG"), ("Statewide-Export", "SWR")):
        path = root / "exports" / f"{stem}.xlsx"
        if not path.exists():
            continue
        for sheet in pd.ExcelFile(path).sheet_names:
            if m := SHEET.match(REPEAT.sub("", sheet)):
                out[m["race"]] = "BQ" if BALLOT_QUESTION.match(sheet) else kind
    return out
