"""The National Governors Association's pages on South Dakota's governors: the list of
former governors and each governor's page."""

from __future__ import annotations

import re
from datetime import datetime

from records.sources._tables import LayoutChanged, document, text

LIST = "https://www.nga.org/former-governors/south-dakota/"


def _date(value: str) -> str | None:
    try:
        return datetime.strptime(" ".join(value.split()), "%B %d, %Y").date().isoformat()
    except ValueError:
        return None


def governors(html: bytes | str) -> list[dict]:
    """The former governors the list shows: name, page, terms (years) and party."""
    root = document(html)
    rows = root.cssselect("table tbody tr")
    if not rows:
        raise LayoutChanged("no governors table")
    out = []
    for tr in rows:
        cells = tr.findall("td")
        link = cells[0].find(".//a[@href]")
        if link is None or len(cells) < 4:
            continue
        out.append({"url": link.get("href"), "listed_name": text(link),
                    "listed_terms": [t.strip() for t in cells[2].itertext() if t.strip()],
                    "listed_party": text(cells[3])})  # fmt: skip
    return out


def current_governor(html: bytes | str) -> str | None:
    """The page of the current governor, from the sidebar every governor's page has."""
    for group in document(html).cssselect(".page-publication__sidebar__group"):
        title = group.cssselect(".title")
        if title and text(title[0]).startswith("Current"):
            link = group.find(".//a[@href]")
            return link.get("href") if link is not None else None
    return None


def governor(html: bytes | str) -> dict:
    """A governor's page: name, every labeled fact as published (terms also as dates),
    the biography, its sources and the portrait."""
    root = document(html)
    h1 = root.cssselect("h1.title")
    if not h1:
        raise LayoutChanged("no governor's name")
    name = text(h1[0])
    facts: dict = {}
    for item in root.cssselect("ul.list--horizontal li.item"):
        label = item.find("label")
        if label is None:
            continue
        lines = [" ".join(t.split()) for t in item.itertext() if t.strip()][1:]
        facts[text(label)] = lines if len(lines) > 1 else (lines[0] if lines else "")
    raw_terms = facts.get("Terms") or []
    raw_terms = [raw_terms] if isinstance(raw_terms, str) else raw_terms
    terms = []
    for term in raw_terms:
        start, _, end = term.partition(" - ")
        terms.append({"start": _date(start), "end": _date(end), "as_published": term})
    about, sources = "", []
    for section in root.cssselect("section.page-publication__article__section"):
        heading = section.cssselect("h2")
        title = text(heading[0]) if heading else ""
        if title == "About":
            about = "\n\n".join(text(p) for p in section.findall(".//p"))
        elif title == "Source":
            for p in section.findall(".//p"):
                link = p.find(".//a[@href]")
                sources.append({"text": text(p), "url": link.get("href") if link is not None
                                else None})  # fmt: skip
    portrait = root.cssselect(".hero-post__img-container img")
    return {"name": re.sub(r"^Gov\.\s+", "", name), "title_name": name, "facts": facts,
            "terms": terms, "party": facts.get("Party"), "born": _date(facts.get("Born", "")),
            "died": _date(facts.get("Passed", "")), "about": about, "sources": sources,
            "portrait": portrait[0].get("src") if portrait else None}  # fmt: skip
