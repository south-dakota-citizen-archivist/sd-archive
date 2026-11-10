"""Fillable PDF forms, read by where answers sit on the page.

Field names differ between versions of a form and are often mangled ('T me' for Time), so
an answer is matched to the printed text around it instead:

- a text field: the label just to its left on the same line ("Recorded By:")
- a checked box: the option printed just to its right ("☑ UST")
- either one: the section it's in, from where each section's label starts
  (section_starts), which tells apart a question asked twice on one form

Coordinates are pdfplumber's: PDF points from the page's top left.
"""

from __future__ import annotations

import re
from collections.abc import Iterable


def text_value(value) -> str:
    """A PDF string as text (UTF-16 with a byte-order mark, else Latin-1)."""
    if isinstance(value, bytes):
        if value.startswith(b"\xfe\xff"):
            return value[2:].decode("utf-16-be", "replace")
        return value.decode("latin-1")
    return str(value or "")


def clean(value) -> str:
    return " ".join(text_value(value).split())


def key(label: str) -> str:
    """A label as a snake_case key: 'Spill Date (MM/DD/YY)' → 'spill_date_mm_dd_yy'."""
    return re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")


def widgets(page) -> list[dict]:
    """Every filled-in field on a pdfplumber page: text fields with a value (type "text",
    with "value") and checkboxes that are checked (type "check"), with their boxes."""
    out = []
    for a in page.annots or []:
        d = a.get("data") or {}
        parent = d.get("Parent")
        kind = str(d.get("FT") or (parent.get("FT") if isinstance(parent, dict) else None))
        name = clean(d.get("T"))
        box = {"x0": a["x0"], "x1": a["x1"], "top": a["top"], "bottom": a["bottom"], "name": name}
        if "Tx" in kind:
            value = clean(d.get("V"))
            if value:
                out.append({**box, "type": "text", "value": value})
        elif "Btn" in kind:
            state = text_value(d.get("AS") or d.get("V") or "").strip("/'")
            if state and state.lower() not in ("off", "no"):
                out.append({**box, "type": "check"})
    return out


def has_fields(page) -> bool:
    return any("Widget" in str((a.get("data") or {}).get("Subtype")) for a in page.annots or [])


def upright_words(page) -> list[dict]:
    """The page's words, without sideways text (a section title printed on its side)."""
    return [w for w in page.extract_words(extra_attrs=["upright"]) if w.get("upright", True)]


def mid(box) -> float:
    return (box["top"] + box["bottom"]) / 2


def lines(words: list[dict], tolerance: float = 4) -> list[list[dict]]:
    """Words grouped into printed lines, each left to right."""
    out: list[list[dict]] = []
    for w in sorted(words, key=lambda w: (mid(w), w["x0"])):
        if out and abs(mid(out[-1][0]) - mid(w)) < tolerance:
            out[-1].append(w)
        else:
            out.append([w])
    return [sorted(line, key=lambda w: w["x0"]) for line in out]


def same_line(words: list[dict], top: float, bottom: float, tolerance: float = 6) -> list[dict]:
    m = (top + bottom) / 2
    return sorted((w for w in words if abs(mid(w) - m) < tolerance), key=lambda w: w["x0"])


def label_left(words: list[dict], field: dict) -> str:
    """The printed label just left of a field: the nearest colon-ended phrase ('Spill
    Date:'), without a note after it ('(MM/DD/YY)')."""
    # a tall box (a paragraph answer) is labeled level with its first line, not its middle
    bottom = min(field["bottom"], field["top"] + 12)
    left = [w["text"] for w in same_line(words, field["top"], bottom, tolerance=8)
            if w["x1"] <= field["x0"] + 2]  # fmt: skip
    colons = [i for i, w in enumerate(left) if w.endswith(":")]
    if not colons:
        return " ".join(left[-4:]).strip()
    end = colons[-1]
    start = end
    while start > 0 and not left[start - 1].endswith(":") and not left[start - 1].startswith("("):
        start -= 1
    return " ".join(left[start : end + 1]).rstrip(":").strip()


def option_right(words: list[dict], box: dict, options: Iterable[str]) -> str:
    """The option printed just right of a checkbox; the longest of `options` it starts with,
    else its first word."""
    right = " ".join(w["text"] for w in same_line(words, box["top"], box["bottom"])
                     if w["x0"] >= box["x1"] - 2)  # fmt: skip
    for option in sorted(options, key=len, reverse=True):
        if right.lower().startswith(option.lower()):
            return option
    return right.split(" ")[0] if right else ""


def section_starts(
    words: list[dict], sections: Iterable[tuple[str, Iterable[str]]]
) -> list[tuple[str, float]]:
    """Where each section begins, given (name, labels) top to bottom: the first line, below
    the previous section's, that opens with one of its labels."""
    found = [(line[0]["top"], " ".join(w["text"] for w in line)) for line in lines(words)]
    starts: list[tuple[str, float]] = []
    for name, labels in sections:
        for top, text in found:
            if starts and top <= starts[-1][1]:
                continue
            if any(re.match(re.escape(lab) + r"\b", text) for lab in labels):
                starts.append((name, top))
                break
    return starts


def section_at(y: float, starts: list[tuple[str, float]], slack: float = 8) -> str:
    """The section a field is in, by its middle (fields sit a little above their label)."""
    current = ""
    for name, top in starts:
        if y >= top - slack:
            current = name
    return current
