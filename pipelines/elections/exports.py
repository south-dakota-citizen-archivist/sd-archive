"""Read the site's export spreadsheets (ResultsExport.aspx), which capture.py saves raw.

Each county's workbook has a sheet per race on that county's ballot, named by the race ID
and the start of its name ("4265Presidential Electors"). On each sheet, after a header block
(election, "Precincts 735 of 735 Reporting", "Aurora County", "Downloaded at …"):

    <race name>\\n<district or county>   Precinct      <candidate>   <candidate> …
                                         Precinct-01   430           286
                                         …
                                         TOTALS        2,323         …

2012's exports have the votes for each precinct but leave the precinct names blank; those
rows get precinct "" and their order on the sheet (precinct_seq). Sheets whose name ends in
"RECOUNT" (or the start of it, cut off at 31 characters) are recounts, read but marked
(recount=True) and not used to fill or check.

The statewide and legislative workbooks are by county, and aren't read here: the county
workbooks have the same races in more detail.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

SKIP = {"Statewide-Export", "Legislative-Export"}
# Recount sheets end in RECOUNT, cut short by Excel's 31-character limit: "…  RE", "…  RECO"
SHEET = re.compile(r"^(?P<race>\d+)(?P<name>.*?)(?P<recount>\s+RE(?:C|CO|COU|COUN|COUNT)?)?$")


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


def read_workbook(path: Path) -> list[dict]:
    """Every row of one county's workbook, with its race ID, sheet and county."""
    out = []
    sheets = pd.read_excel(path, sheet_name=None, header=None, dtype=str)
    for sheet, frame in sheets.items():
        m = SHEET.match(sheet)
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
                    "recount": bool(m["recount"]),
                    "county": county,
                    "file": path.name,
                    "sheet": sheet,
                    **r,
                }
            )
    return out


def read_election(root: Path) -> list[dict]:
    """Every county workbook captured for one election (root = raw/<eid>)."""
    out = []
    for path in sorted((root / "exports").glob("*.xlsx")):
        if path.stem not in SKIP:
            out += read_workbook(path)
    return out
