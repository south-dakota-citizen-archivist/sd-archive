"""The boards' disciplinary action pages: each action an entry (a list item or paragraph, its
parts on lines split by <br>), under a heading or list item that gives its year ("2023
Board Actions", "2024 disciplinary action", "The final disciplinary actions for 2016 are as
follows:"). Each board writes its entries its own way, so each has its reader; an entry that
looks like an action (an action's word and a date) and that no reader reads is returned
apart, for the run to report.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import lxml.html

BLOCKS = {"p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "div", "ul", "ol", "section"}
NONE = re.compile(r"\bno disciplinary actions?\b|\bnone reported\b", re.I)
YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
ACTION_WORDS = re.compile(r"revo|surrender|reprimand|probation|suspen|consent|stipulat|order|"
                          r"refrain", re.I)  # fmt: skip
DATE = re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b|\b[A-Z][a-z]+\.? \d{1,2},? \d{4}\b|"
                  r"\b[A-Z][a-z]+ \d{4}\b")  # fmt: skip
SUFFIXES = {"Jr", "Jr.", "Sr", "Sr.", "II", "III", "IV"}


@dataclass
class Entry:
    lines: list[str]
    year: int | None  # from the heading it's under
    links: list[tuple[str, str]] = field(default_factory=list)  # (text, href)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    @property
    def flat(self) -> str:
        return " ".join(self.lines)


def _own_text(el) -> str:
    """An element's text outside its nested lists (a list item heading its sublist)."""
    parts = [el.text or ""]
    for child in el:
        if isinstance(child.tag, str) and child.tag in ("ul", "ol"):
            break
        parts.append(child.text_content() if isinstance(child.tag, str) else "")
        parts.append(child.tail or "")
    return " ".join("".join(parts).split())


def _lines(el) -> list[str]:
    for br in el.iter("br"):
        br.tail = "\n" + (br.tail or "")
    return [" ".join(x.split()) for x in el.text_content().split("\n") if x.strip()]


def entries(html: str) -> list[Entry]:
    """The page's entries, in order, with the year each is under. The page's own text (an
    introduction, "This site is updated quarterly") comes as entries too; readers skip it."""
    doc = lxml.html.fromstring(html)
    content = doc.xpath("//div[contains(@class,'site-content')]")
    if not content:
        raise ValueError("no page content (the page's layout changed)")
    out, year = [], None
    for el in content[0].iter():
        if not isinstance(el.tag, str):
            continue
        nested = el.tag == "li" and el.xpath(".//li")
        if el.tag in ("h2", "h3", "h4", "h5") or nested:
            m = YEAR.search(_own_text(el))
            if m:
                year = int(m[0])
            continue
        leaf = (el.tag == "li" and not nested) or (el.tag == "p" and not el.xpath("ancestor::li"))
        if not leaf:
            continue
        lines = _lines(el)
        if not lines:
            continue
        own_year = (re.match(r"^((?:19|20)\d{2})\b", lines[0])
                    or re.search(r"actions for ((?:19|20)\d{2})", lines[0]))  # fmt: skip
        if own_year:
            year = int(own_year[1])
        links = [(" ".join(a.text_content().split()), a.get("href")) for a in el.iter("a")
                 if a.get("href")]  # fmt: skip
        out.append(Entry(lines, year, links))
    return out


def looks_like_an_action(entry: Entry) -> bool:
    return (not NONE.search(entry.flat) and bool(ACTION_WORDS.search(entry.flat))
            and bool(DATE.search(entry.flat)))  # fmt: skip


def _who(name: str) -> tuple[str, str | None]:
    """ "Richard A. Ward, III" is a name; "James Sneeden, Watertown" a name and a town."""
    name, _, rest = name.partition(",")
    rest = rest.strip()
    if rest and rest.rstrip(".") in {s.rstrip(".") for s in SUFFIXES}:
        return f"{name.strip()}, {rest}", None
    return name.strip(), rest or None


# --- each board's entries -----------------------------------------------------------------


def optometry(e: Entry) -> dict | None:
    """ "2017 - Jeffrey Browen, OD - 12 Month Probation - Effective Date: October 2, 2017" """
    m = re.match(r"^(?P<year>\d{4}) - (?P<name>.+?)(?:, (?P<credential>OD))? - (?P<action>.+?)"
                 r"\s*-\s*(?P<label>Effective Date|Consent Agreement): (?P<date>.+?)"
                 r"(?: - End Date: (?P<ended>.+))?$", e.flat)  # fmt: skip
    if not m:
        return None
    return {"name": m["name"], "credential": m["credential"], "action": m["action"],
            "effective": m["date"], "ended": m["ended"]}  # fmt: skip


def speech_language_pathology(e: Entry) -> dict | None:
    """ "… for 2020 are as follows: Vanessa Anderson-Smith #342-SLP – Letter of Reprimand
    effective 05/29/2020." """
    m = re.search(r"(?P<name>[A-Z][^:#]+?) #(?P<number>[\w-]+) [–-] (?P<action>.+?) effective "
                  r"(?P<date>[\d/]+)", e.flat)  # fmt: skip
    if not m:
        return None
    return {"name": m["name"], "license_number": m["number"], "action": m["action"],
            "effective": m["date"]}  # fmt: skip


def podiatry(e: Entry) -> dict | None:
    """ "Todd Monroe DPM #121 – License Revoked effective July 7, 2011" """
    m = re.match(r"^(?P<name>.+?) (?P<credential>DPM) #(?P<number>\w+) [–-] (?P<action>.+?) "
                 r"effective (?P<date>.+)$", e.flat)  # fmt: skip
    if not m:
        return None
    return {"name": m["name"], "credential": m["credential"], "license_number": m["number"],
            "action": m["action"], "effective": m["date"]}  # fmt: skip


def funeral(e: Entry) -> dict | None:
    """ "Taylor Oster, Funeral Embalmer #1388 — Consent Order - 11/17/22", "Jessica Scott -
    Voluntary Surrender of License - 10/17/2024", "… — License Revoked effective 3/16/09" """
    m = re.match(r"^(?P<who>.+?) [—–-] (?P<action>.+?)(?: effective | - )(?P<date>\S.*)$",
                 e.flat)  # fmt: skip
    if not m:
        return None
    who = re.match(r"^(?P<name>[^,]+)(?:, (?P<credential>[^#]+?)\s*(?:#\s?(?P<number>\w+))?)?$",
                   m["who"])  # fmt: skip
    if not who:
        return None
    return {"name": who["name"].strip(), "credential": who["credential"],
            "license_number": who["number"], "action": m["action"],
            "effective": m["date"]}  # fmt: skip


def audiology(e: Entry) -> dict | None:
    """The parts run together: "James Sneeden, WatertownHearing Aid Dispenser # 313HLicense
    expired 06/30/14Letter of Reprimand effective March 6, 2009"; "Eric Smith, Hearing Aid
    Dispenser #428-H, Revocation effective 11/6/25 | Decision & Order"."""
    m = re.match(r"^(?P<who>.+?),\s*(?P<place>[A-Za-z .]*?)\s*(?P<credential>Hearing Aid "
                 r"Dispenser|Audiologist)\s*#\s?(?P<number>\d+-?H?)\W*(?:License expired "
                 r"(?P<expired>[\d/]+))?\s*(?P<action>.+?) effective (?P<date>[^|]+?)\s*"
                 r"(?:\|.*)?$", e.flat)  # fmt: skip
    if not m:
        return None
    name, town = _who(f"{m['who']}, {m['place']}" if m["place"] else m["who"])
    out = {"name": name, "credential": m["credential"], "license_number": m["number"],
           "action": m["action"], "effective": m["date"], "city": town}  # fmt: skip
    if m["expired"]:
        out["details"] = {"license expired": m["expired"]}
    return out


def chiropractic(e: Entry) -> dict | None:
    """ "Joel C. Martens DC - Hartford, SD - License #1327 / Board Action: September 28, 2023 –
    Consent Agreement and Order" """
    m = re.match(r"^(?P<name>.+?),? (?P<credential>DC) - (?P<city>[^,]+), (?P<state>[A-Z]{2}) "
                 r"- ?License # ?(?P<number>\d+)\s*Board Action: (?P<date>.+?) [–-] "
                 r"(?P<action>.+)$", e.flat)  # fmt: skip
    if not m:
        return None
    return {"name": m["name"], "credential": m["credential"], "license_number": m["number"],
            "city": m["city"].strip(), "state": m["state"], "action": m["action"],
            "effective": m["date"]}  # fmt: skip


def nursing_facility_administrators(e: Entry) -> dict | None:
    """Five lines: name, town (or facility), action, "Effective … [License reinstated …]",
    "Nature of complaint: …"."""
    if len(e.lines) != 5 or not e.lines[3].startswith("Effective"):
        return None
    name, where, action, when, nature = e.lines
    m = re.match(r"^Effective (?P<date>[^.]+)\.?(?: License reinstated (?P<ended>[^.]+)\.?)?$",
                 when)  # fmt: skip
    if not m:
        return None
    place = re.match(r"^(?P<city>[^,]+), (?P<state>[A-Z]{2})$", where)
    return {"name": name.rstrip(", "), "action": action, "effective": m["date"],
            "ended": m["ended"], "city": place["city"] if place else None,
            "state": place["state"] if place else None, "location": where,
            "nature": re.sub(r"^Nature of complaint:\s*", "", nature, flags=re.I)}  # fmt: skip
