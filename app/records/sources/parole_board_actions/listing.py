"""The Board of Pardons and Paroles' meetings on boardsandcommissions.sd.gov (BoardID 74):
each meeting's date, description and documents, a year to a page (the year list posts back);
and which documents are the board's action reports."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime

import lxml.html

REPORT = re.compile(r"action|decision", re.I)  # "… Public All-Action Report", "Board actions …"
NOT_REPORT = re.compile(r"next parole date|schedule|agenda|minutes", re.I)


@dataclass
class Document:
    title: str
    url: str
    file_type: str
    uploaded: str


@dataclass
class Meeting:
    date: date | None
    date_as_listed: str
    title: str
    description: str
    location: str
    documents: list[Document] = field(default_factory=list)


def _label(pane, label: str) -> str:
    """The text after a pane's <strong>Label:</strong>, up to the next <br>."""
    for strong in pane.iter("strong"):
        if strong.text_content().strip().rstrip(":") == label:
            text, node = strong.tail or "", strong.getnext()
            while node is not None and node.tag != "br":
                text += node.text_content() + (node.tail or "")
                node = node.getnext()
            return " ".join(text.split())
    return ""


def meetings(html: str | bytes) -> list[Meeting]:
    root = lxml.html.fromstring(html)
    out = []
    for header in root.xpath('//div[contains(@class, "accheader")]'):
        content = header.getnext()
        if content is None or "acccontent" not in (content.get("class") or ""):
            continue
        listed = " ".join((header.xpath('.//span[contains(@id, "lblHeaderDate")]/text()')
                           or [""])[0].split())  # fmt: skip
        title = " ".join(header.text_content().split()).split("|", 1)[-1].strip()
        try:
            when = datetime.strptime(listed, "%m/%d/%Y").date()
        except ValueError:
            when = None
        docs = []
        for row in content.xpath('.//table[contains(@id, "gvDocuments")]//tr[td]'):
            cells = row.xpath("./td")
            link = row.xpath(".//a[@href]")
            if not link or len(cells) < 3:
                continue
            docs.append(Document(" ".join(link[0].text_content().split()), link[0].get("href"),
                                 cells[1].text_content().strip(),
                                 cells[2].text_content().strip()))  # fmt: skip
        out.append(Meeting(when, listed, title, _label(content, "Description"),
                           _label(content, "Location"), docs))  # fmt: skip
    return out


def is_report(doc: Document) -> bool:
    return bool(REPORT.search(doc.title)) and not NOT_REPORT.search(doc.title)


def years(html: str | bytes) -> list[int]:
    root = lxml.html.fromstring(html)
    return sorted(int(v) for v in root.xpath('//select[@id="ddlArchiveYear"]/option/@value')
                  if v.isdigit())  # fmt: skip


def form(html: str | bytes) -> dict[str, str]:
    """The page's hidden form fields, for posting back a year."""
    root = lxml.html.fromstring(html)
    return {i.get("name"): i.get("value") or "" for i in root.xpath('//input[@type="hidden"]')
            if i.get("name")}  # fmt: skip
