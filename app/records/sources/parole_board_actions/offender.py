"""Read the older action reports, "Parole Board Decisions by Offender" (2015–2020, with a
text layer): each case is a ruled box, its cells in columns under the header.

DOC # | Name / Location | Race / Sex | OS/NS | Crime | County | Transaction(s) |
Decision / Type | Details | Next Review

A case can list several crimes, each with its county code and transaction number on the line
the crime starts; a crime's text can wrap onto the next lines. The Decision / Type cell is
the decision ("Deny Earned Final Discharge") then the hearing type ("Earned Discharge
Hearing"), wrapped over lines; the two are split by the hearing types the reports use.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

COLUMNS = ["doc", "name", "race", "os", "crime", "county", "transaction", "decision",
           "details", "review"]  # fmt: skip
# how far left of its header a column's values start, at most (OS/NS 9 points; crimes 13,
# and OS/NS ends 27 left of the Crime header)
MARGIN = {"os": 12, "crime": 20}


class LayoutChanged(Exception):
    pass


@dataclass
class Case:
    page: int
    cells: dict[str, list[str]] = field(default_factory=dict)  # column → lines
    crimes: list[dict] = field(default_factory=list)


def _header(words: list[dict]) -> dict[str, dict]:
    """Each column's header, its left edge the leftmost of its header words ("Location"
    starts left of "Name", and names start there)."""
    want = {"DOC": "doc", "Name": "name", "Location": "name", "Race": "race", "Sex": "race",
            "OS/": "os", "NS": "os", "Crime": "crime", "County": "county",
            "Transaction(s)": "transaction", "Decision": "decision", "Details": "details",
            "Next": "review", "Review": "review"}  # fmt: skip
    anchor = next((w for w in words if w["text"] == "Transaction(s)"), None)
    if anchor is None:
        raise LayoutChanged("no Transaction(s) header")
    found: dict[str, dict] = {}
    for w in words:
        key = want.get(w["text"])
        if not key or abs(w["top"] - anchor["top"]) > 14:  # the header's two lines only
            continue
        if key not in found:
            found[key] = dict(w)
        elif key not in ("decision", "details", "transaction"):  # centered: keep the label's
            found[key]["x0"] = min(found[key]["x0"], w["x0"])
    if len(found) != len(set(want.values())):
        raise LayoutChanged(f"header words missing: {sorted(set(want.values()) - set(found))}")
    return found


def _column(w: dict, h: dict[str, dict]) -> str:
    x0, mid = w["x0"], (w["x0"] + w["x1"]) / 2
    details_center = (h["details"]["x0"] + h["details"]["x1"]) / 2
    if re.fullmatch(r"\d{2}-[A-Z]{3}-\d{2}", w["text"]) and x0 > details_center:
        return "review"  # "01-AUG-16" starts 9 points left of its header
    if x0 >= h["review"]["x0"]:
        return "review"
    center = {k: (h[k]["x0"] + h[k]["x1"]) / 2 for k in ("transaction", "decision", "details")}
    if mid >= (center["decision"] + center["details"]) / 2:
        return "details"
    if mid >= (center["transaction"] + center["decision"]) / 2:
        return "decision"
    if x0 < h["race"]["x0"] - 6:  # the DOC number, then the name and location under it
        return "doc" if re.fullmatch(r"\d+", w["text"]) and x0 < h["name"]["x0"] - 2 else "name"
    if x0 < h["crime"]["x0"] - MARGIN["crime"]:
        # Race / Sex and OS/NS are centered: split at the midpoint of their headers
        centers = {k: (h[k]["x0"] + h[k]["x1"]) / 2 for k in ("race", "os")}
        return "race" if mid < (centers["race"] + centers["os"]) / 2 else "os"
    for col, nxt in zip(COLUMNS[4:], COLUMNS[5:], strict=False):
        if nxt == "decision":
            return "transaction"
        if x0 < h[nxt]["x0"] - MARGIN.get(nxt, 6):  # values start left of their headers
            return col
    return "transaction"


def _lines(words: list[dict]) -> list[tuple[float, str]]:
    rows: list[list[dict]] = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if rows and abs(rows[-1][0]["top"] - w["top"]) < 3:
            rows[-1].append(w)
        else:
            rows.append([w])
    return [(r[0]["top"], " ".join(w["text"] for w in sorted(r, key=lambda w: w["x0"])))
            for r in rows]  # fmt: skip


def _boxes(page, header_bottom: float) -> list[tuple[float, float]]:
    """The cases' boxes, top to bottom. Each is drawn twice (outer and inner, a few points
    apart), and some pages draw a frame around several: a box holding two others is a frame,
    not a case."""
    boxes = sorted({(round(r["top"]), round(r["bottom"])) for r in page.rects
                    if r["width"] > page.width * 0.6 and r["height"] > 20
                    and r["top"] > header_bottom}, key=lambda b: b[0])  # fmt: skip

    def holds(a, b):
        return (
            a != b and a[0] <= b[0] + 1 and b[1] <= a[1] + 1 and (b[1] - b[0]) < (a[1] - a[0]) - 8
        )

    boxes = [a for a in boxes if sum(holds(a, b) for b in boxes) < 2]
    out: list[tuple[float, float]] = []
    for top, bottom in boxes:
        if out and abs(top - out[-1][0]) <= 6 and abs(bottom - out[-1][1]) <= 6:
            out[-1] = (min(out[-1][0], top), max(out[-1][1], bottom))  # its twin
        elif out and top < out[-1][1] - 2 and bottom <= out[-1][1] + 2:
            continue  # inside the one before (its inner)
        else:
            out.append((top, bottom))
    return out


SUMMARY = re.compile(r"BOARD DECISIONS REPORT", re.I)
OUTCOME = re.compile(r"([A-Z][A-Za-z/ -]*?|N/A) (\d+)(?= |$)")


def summary(text: str) -> dict:
    """The report's last page: "Total Inmates:230" and the totals per hearing outcome."""
    total = re.search(r"Total Inmates:\s*(\d+)", text)
    block = re.search(r"Totals Per Hearing Outcome:(.*?)Search Parameters", text, re.S)
    outcomes = {}
    for line in (block[1] if block else "").splitlines():
        for m in OUTCOME.finditer(line.strip()):
            outcomes[m[1].strip()] = int(m[2])
    dates = re.search(r"Date Range:\s*(\S+)\s+TO\s+(\S+)", text)
    return {"total_inmates": int(total[1]) if total else None, "outcomes": outcomes,
            "date_range": [dates[1], dates[2]] if dates else None}  # fmt: skip


# The Decision / Type cell's words, as the boxes print them (the summary page names some
# decisions differently: its "Grant Earned Final Discharge" is the boxes' "Grant Early Final
# Discharge", and its "Grant" includes "Grant Partial Early Discharge")
DECISIONS = [
    "Grant", "Deny", "Revoke", "Continue", "Waived", "Reject", "Non-Compliant",
    "Non-Compliant Granted", "Revoke and Reinstate", "Grant Early Final Discharge",
    "Deny Early Final Discharge", "Grant Earned Final Discharge", "Deny Earned Final Discharge",
    "Grant Partial Early Discharge", "Deny Partial Early Discharge",
    "Grant Partial Earned Discharge", "Deny Partial Earned Discharge",
    "Continue to Paper Review Hearing", "Recommend to Personal Appearance",
    "Proceed to Revocation Hearing", "Revoke and Establish New Date", "Waived Hearing",
    "Deny and Dismiss Appeal", "Grant and Establish New Date", "Reviewed",
    "Proceed to Contested Hearing", "Recommend to Governor", "Not Recommended", "Compliant",
]  # fmt: skip
HEARING_TYPES = [
    "Parole Hearing", "PV Waiver Hearing", "SSV Waiver Hearing", "Non-compliance Hearing",
    "PV Revocation Hearing", "SSV Revocation Hearing", "Early Discharge Hearing",
    "Earned Discharge Hearing", "Commutation- Paper Review Hearing",
    "Commutation- Personal Appearance Hearing", "Appeal of Parole Date Hearing",
    "Dispositional Meeting", "Suspended Sentence Dispositional Meeting", "Remand Hearing",
    "Compassionate Parole", "Case Review Hearing", "Withholding Parole", "Rescission Hearing",
    "Preliminary Hearing", "Probable Cause Preliminary Hearing",
]  # fmt: skip


def _flat(text: str) -> str:
    """A cell's lines as one string: a word broken at its hyphen ("Non- / compliance") is
    joined, and "Commutation- Paper" keeps its space, as the reports print it."""
    text = re.sub(r"\bNon- compliance\b", "Non-compliance", " ".join(text.split()))
    return text


def hearings(cell: list[str]) -> tuple[list[tuple[str | None, str]], str]:
    """The (decision, hearing type) pairs a Decision / Type cell lists, and what's left that
    isn't one (empty when the cell reads whole). A pair without a decision is a hearing the
    summary page counts as N/A."""
    text, out = _flat(" ".join(cell)), []
    by_length = lambda words: sorted(words, key=len, reverse=True)  # noqa: E731
    while text:
        decision = next((d for d in by_length(DECISIONS)
                         if text == d or text.startswith(d + " ")), None)  # fmt: skip
        rest = text[len(decision) :].strip() if decision else text
        kind = next((t for t in by_length(HEARING_TYPES)
                     if rest == t or rest.startswith(t + " ")), None)  # fmt: skip
        if kind is None:
            if decision and decision.endswith("Hearing"):  # "Continue to Paper Review Hearing"
                out.append((decision, None))
                text = rest
                continue
            return out, text
        out.append((decision, kind))
        text = rest[len(kind) :].strip()
    return out, ""


def cases(pdf) -> list[Case]:
    out = []
    for n, page in enumerate(pdf.pages, start=1):
        words = page.extract_words(x_tolerance=1.5)
        if SUMMARY.search(page.extract_text() or "") and not any(
                w["text"] == "Transaction(s)" for w in words):  # fmt: skip
            continue  # the summary page
        h = _header(words)
        header_bottom = max(w["bottom"] for w in h.values())
        for top, bottom in _boxes(page, header_bottom + 2):
            inside = [w for w in words if top <= (w["top"] + w["bottom"]) / 2 <= bottom]
            if not inside:
                continue
            case = Case(page=n)
            by_col: dict[str, list[dict]] = {}
            for w in inside:
                by_col.setdefault(_column(w, h), []).append(w)
            for col in COLUMNS:
                case.cells[col] = [text for _, text in _lines(by_col.get(col, []))]
            # crimes: a new one starts on each line its county code is on
            starts = [t for t, _ in _lines(by_col.get("county", []))]
            crime_lines = _lines(by_col.get("crime", []))
            txn = dict(_lines(by_col.get("transaction", [])))
            county = dict(_lines(by_col.get("county", [])))
            for t, text in crime_lines:
                start = next((s for s in starts if abs(s - t) < 3), None)
                if start is not None or not case.crimes:
                    number = next((v for k, v in txn.items() if abs(k - t) < 3), None)
                    code = county.get(start) if start else None
                    case.crimes.append({"crime": text, "transaction": number,
                                        "county_code": code})  # fmt: skip
                else:
                    case.crimes[-1]["crime"] += " " + text
            if not case.cells["doc"] and out:  # a case's box run onto the next page
                prev = out[-1]
                for col, lines in case.cells.items():
                    prev.cells[col] = prev.cells.get(col, []) + lines
                if case.crimes and prev.crimes and not case.crimes[0]["county_code"]:
                    prev.crimes[-1]["crime"] += " " + case.crimes.pop(0)["crime"]
                prev.crimes += case.crimes
                continue
            out.append(case)
    return out
