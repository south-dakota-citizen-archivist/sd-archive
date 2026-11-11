"""The Unified Judicial System's "South Dakota Supreme Court Photographic History" (a PDF):
its list of every member of the Supreme Court and its list of chief justices, each set in
two columns. The pages are found by their headings, not their numbers."""

from __future__ import annotations

import io
import re

from records.sources._tables import LayoutChanged

URL = "https://ujs.sd.gov/media/ge2bfzs4/supreme-court-photographic-history.pdf"
MEMBERS = "Members of the South Dakota Supreme Court"
CHIEFS = "Chief Justices of the South Dakota Supreme Court"
# "*+James H. McCoy, District 5 1909 – 1921", "Robert Gusinsky, District 1 2025 –"
LINE = re.compile(r"^(?P<marks>[*+]*)\s*(?P<name>.+?),\s*District\s+(?P<district>\d+)\s+"
                  r"(?P<start>\d{4})\s*[–-]\s*(?P<end>\d{4})?\s*$")  # fmt: skip


def _lines(page) -> list[str]:
    """A two-column page's lines: the left column, then the right."""
    out = []
    for box in ((0, 0, page.width / 2, page.height), (page.width / 2, 0, page.width, page.height)):
        out += (page.crop(box).extract_text() or "").splitlines()
    return out


def _entries(page) -> list[dict]:
    out = []
    for line in _lines(page):
        m = LINE.match(line.strip())
        if m:
            out.append({"name": m["name"].strip(), "marks": m["marks"],
                        "district": int(m["district"]), "start": int(m["start"]),
                        "end": int(m["end"]) if m["end"] else None,
                        "as_published": line.strip()})  # fmt: skip
    return out


def lists(pdf_bytes: bytes) -> tuple[list[dict], list[dict], dict]:
    """(members, chief justices, where each list was: its page number)."""
    import pdfplumber

    members, chiefs, pages = [], [], {}
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            head = (page.extract_text() or "")[:200]
            if MEMBERS in head:
                members, pages["members"] = _entries(page), page.page_number
            elif CHIEFS in head:
                chiefs, pages["chief_justices"] = _entries(page), page.page_number
    if not members:
        raise LayoutChanged(f"no page headed {MEMBERS!r}")
    return members, chiefs, pages
