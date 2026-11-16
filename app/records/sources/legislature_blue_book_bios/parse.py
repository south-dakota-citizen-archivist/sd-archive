"""Read a Blue Book biography (the Legislative Manual's legislator pages, 1991–2023) from its
OCR text into labeled parts.

The layout changed over the years:

- 1991: "JOHN J. "JOE" REEDY. Mailing Address: …. Born: …. Married: …. Children: …." with
  party and district later in the paragraph.
- 1993–2009: "Roger A. Porch -Republican. District 26: Haakon, Jackson … counties.
  Address: …. Born: (or Date of Birth:) …. Married: (or Spouse:) …. Children: …." then
  religion, schools, work and service run on without labels.
- 2011: name; "Republican. District 19."; counties; the address on its own lines, unlabeled;
  then "Date of Birth:" and the rest.
- 2013–2023: "Ried Holien (R) District 5", then labeled parts: Email, Counties, Address,
  Date of Birth, Spouse, Children, Religion, Education, Experience, Legislative Experience,
  Occupation, Committees.

The legislature's OCR repeats some lines four or five times (2019, 2023), with small
differences between the copies; a run of near-identical lines becomes its most common
version. Words broken over lines ("Hutchin-" / "son") are joined.

Only what a label (or the header's fixed shape) marks is split out; everything else stays in
`rest`, as printed. Values are as the OCR reads them, errors included ("SO 57381" for SD).
"""

from __future__ import annotations

import re
from collections import Counter
from difflib import SequenceMatcher

LABELS = {  # label as printed → part
    "Mailing Address": "address", "Address": "address",
    "Date of Birth": "birth", "Born": "birth", "Bom": "birth",  # "Bom": the OCR's Born
    "Spouse": "spouse", "Married": "spouse",
    "Children": "children", "Cbildren": "children",
    "Religion": "religion", "Education": "education",
    "Legislative Experience": "legislative_experience", "Experience": "experience",
    "Occupation": "occupation", "Committees": "committees",
    "E-Mail": "email", "E-mail": "email", "Email": "email", "Counties": "counties",
}  # fmt: skip
LABEL = re.compile(r"(?<![\w-])(" + "|".join(sorted(map(re.escape, LABELS), key=len,
                   reverse=True)) + r")\s*:", re.I)  # fmt: skip
PARTIES = {"R": "Republican", "D": "Democrat", "I": "Independent", "L": "Libertarian"}
PARTY_WORD = re.compile(r"\b(Republican|Democrat(?:ic)?|Independent|Libertarian)\b")
PARTY_LETTER = re.compile(r"\(([RDIL])\)")
DISTRICT = re.compile(r"\b(?:District|Dist\.?)\s*:?\s*(\d{1,2}[AB]?)\b")
EMAIL = re.compile(r"[\w.]+\s*@\s*[\w.]+\.(?:us|gov|com|net|org|edu)", re.I)
SIMILAR = 0.8


def collapse_repeats(lines: list[str]) -> list[str]:
    """Runs of near-identical lines (the OCR's repeats) as one line: the run's most common
    version, the first of those on a tie."""
    out: list[list[str]] = []
    for line in lines:
        line = line.rstrip()
        if out and line and SequenceMatcher(None, out[-1][0], line).ratio() >= SIMILAR:
            out[-1].append(line)
        else:
            out.append([line])
    return [Counter(group).most_common(1)[0][0] for group in out]


def join_lines(lines: list[str]) -> str:
    """One paragraph: a word broken with a hyphen at a line's end is joined up."""
    text = ""
    for line in (raw.strip() for raw in lines if raw.strip()):
        if text.endswith("-") and line[:1].islower():
            text = text[:-1] + line
        else:
            text = f"{text} {line}" if text else line
    text = text.replace("\u00ad ", "").replace("\u00ad", "")  # soft hyphens ("Coun- ties")
    return re.sub(r"\s+", " ", text).strip()


def clean_text(ocr: str) -> str:
    return join_lines(collapse_repeats(ocr.splitlines()))


SENTENCE_END = re.compile(r"(?<!\b[A-Z])(?<!\b(?:Jr|Sr|Dr|St|Mt|Ft))\.\s+(?=[A-Z])")


def first_sentence(value: str) -> tuple[str, str]:
    """A value up to its first sentence end (not an initial's period: "Paul R., Mary L.
    James"), and what follows."""
    m = SENTENCE_END.search(value)
    if not m:
        return value, ""
    return _strip(value[: m.start()]), value[m.end() :].strip()


def _strip(value: str) -> str:
    return value.strip(" .;,").strip()


def read(ocr: str) -> dict:
    """A bio's parts: header (name, party, district, counties as printed), the labeled parts,
    and the rest."""
    lines = collapse_repeats(ocr.splitlines())
    text = join_lines(lines)
    pieces = LABEL.split(text)
    head, parts = pieces[0], {}
    rest = []
    for label, value in zip(pieces[1::2], pieces[2::2], strict=True):
        name = next(v for k, v in LABELS.items() if k.lower() == label.lower())
        value = _strip(value)
        if name in parts:  # a label printed twice ("Experience" in a run-on paragraph)
            rest.append(f"{label}: {value}")
        else:
            parts[name] = value
    out = {"name": None, "party": None, "district": None, "counties": None}
    # the name: up to the party, the district, or the first period/label
    m = re.match(r"\s*(.+?)\s*(?:\(([RDIL])\)|[-–—]\s*(?=Republican|Democrat|Independent|"
                 r"Libertarian)|\.\s+(?=Mailing)|$)", head)  # fmt: skip
    if m:
        out["name"] = _strip(m[1]) or None
    first_line = lines[0].strip() if lines else ""
    if out["name"] and len(out["name"]) > 60:  # 2011: the name is the first line
        out["name"] = _strip(re.sub(r"\(([RDIL])\).*", "", first_line)) or None
    letter = PARTY_LETTER.search(head)
    word = PARTY_WORD.search(head) or PARTY_WORD.search(text)
    if letter:
        out["party"] = PARTIES[letter[1]]
    elif word:
        out["party"] = "Democrat" if word[1].startswith("Democrat") else word[1]
    district = DISTRICT.search(head) or DISTRICT.search(text)
    if district:
        out["district"] = district[1]
    counties = parts.pop("counties", None)
    if counties is None and district:
        in_head = district.end() <= len(head)
        after = (head if in_head else text)[district.end() :].lstrip(" :.,")
        m = re.match(r"(.{2,160}?\b[Cc]ount(?:y|ies))\b[.,]?\s*(.*)", after)
        if m:
            counties = m[1]
            if in_head and m[2].strip() and "address" not in parts:  # 2011: the address
                parts["address"] = _strip(m[2])
        elif in_head and after.strip():  # "District 2: Brown and Marshall."
            counties = after
    out["counties"] = _strip(counties) if counties else None
    for name in ("spouse", "children"):  # before 2010 the paragraph runs on after them
        if parts.get(name):
            value, more = first_sentence(parts[name])
            parts[name] = value
            if more:
                rest.insert(0, more)
    if "email" in parts:
        found = EMAIL.search(parts["email"].replace(" ", ""))
        parts["email"] = found[0] if found else parts["email"]
    out |= parts
    out["rest"] = " ".join(rest) or None
    out["text"] = text
    return out
