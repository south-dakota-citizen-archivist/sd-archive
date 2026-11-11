"""Rules that turn a crawled site into candidate record sets, each with its evidence.

- Platforms: a link to (or page on) a known government records system, by URL: a meetings
  portal, a permit system, a parcel map, a document repository, a code of ordinances.
- Document collections: a page linking many documents (agendas, reports, budgets), with
  the years its file names and link texts mention.
- Record types: pages whose titles, headings or links name a kind of record (permits,
  audits, budgets, inspections…), grouped by type.

Each candidate says what's already covered: a platform the archive already reads.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from .crawl import Page

# name: (URL pattern, what it usually holds, the record set or project already reading it)
PLATFORMS = {
    "CivicClerk": (r"civicclerk\.com", "meeting agendas, minutes and files", None),
    "Granicus / Legistar": (r"granicus\.com|legistar\.com", "meeting agendas, minutes, video",
                            None),
    "CivicPlus Agenda Center": (r"/AgendaCenter", "meeting agendas and minutes", None),
    "CivicPlus Archive Center": (r"/Archive\.aspx", "archived documents (reports, newsletters)",
                                 None),
    "CivicPlus Document Center": (r"/DocumentCenter", "documents", None),
    "CivicPlus Bids": (r"/Bids\.aspx", "bids and requests for proposals", None),
    "BoardDocs": (r"boarddocs\.com", "school board agendas, minutes, policies", None),
    "Simbli (eBoard)": (r"simbli\.eboardsolutions\.com", "school board meetings and policies",
                        None),
    "Laserfiche WebLink": (r"/WebLink/|laserfiche", "a public document repository", None),
    "Municode": (r"municode\.com", "code of ordinances", None),
    "American Legal Publishing": (r"amlegal\.com", "code of ordinances", None),
    "Beacon": (r"beacon\.schneidercorp\.com", "parcels, assessments, sales", "beacon-parcels"),
    "ArcGIS": (r"arcgis\.com|/arcgis/rest/services|/portal/apps/", "GIS layers and maps",
               "GIS hub"),
    "OpenGov / ViewpointCloud": (r"viewpointcloud\.com|opengov\.com",
                                 "permits, licenses, inspections, budgets", None),
    "iWorQ": (r"iworq\.net", "permits, code enforcement, work orders", None),
    "Tyler Technologies": (r"tylerhost\.net|tylertech\.com|tylerportico", "court, permit or "
                           "finance records", None),
    "SeeClickFix": (r"seeclickfix\.com", "service requests", None),
    "Socrata / CKAN data portal": (r"socrata\.com|/ckan/|data\.[a-z]+\.(gov|us)",
                                   "open data", None),
    "BurialSearch": (r"burialsearch\.com", "cemetery burials", "cemetery-burials"),
    "SD local government portal": (r"sdlocalgov\.appssd\.sd\.gov", "meetings",
                                   "local-government-meetings"),
}  # fmt: skip

# record type: words in titles, headings and link texts that name it
RECORD_TYPES = {
    "meeting agendas and minutes": r"\b(agendas?|minutes|meetings?)\b",
    "budgets": r"\bbudgets?\b",
    "audits and financial reports": r"\b(audits?|financial (statements?|reports?)|acfr|cafr)\b",
    "bills paid / claims": r"\b(claims|warrants|bills paid|disbursements|expenditures?)\b",
    "building permits": r"\b(building permits?|permit reports?)\b",
    "permits and licenses": r"\b(permits?|licen[cs]es?)\b",
    "inspections": r"\binspections?\b",
    "ordinances and resolutions": r"\b(ordinances?|resolutions?)\b",
    "bids, RFPs and contracts": r"\b(bids?|rfps?|requests? for proposals?|contracts?)\b",
    "planning and zoning applications": r"\b(zoning|variances?|plats?|planning commission|"
                                        r"conditional use)\b",
    "elections": r"\b(elections?|ballots?|candidates?)\b",
    "property assessment": r"\b(assessments?|assessor|equalization|tax levy)\b",
    "public safety reports": r"\b(police|sheriff|crime|arrests?|incident|calls for service)\b",
    "water quality reports": r"\b(water quality|consumer confidence)\b",
    "plans and studies": r"\b(comprehensive plan|capital improvement|master plan|studies|"
                         r"study)\b",
    "public notices": r"\b(public notices?|legal notices?|hearings?)\b",
    "salaries and staff": r"\b(salar(y|ies)|wages?|employees?|staff directory)\b",
    "annual reports": r"\bannual reports?\b",
    "public records requests": r"\b(public records|open records|records requests?|foia)\b",
}  # fmt: skip
DOCS_FOR_A_COLLECTION = 5
YEAR = re.compile(r"\b(19[5-9]\d|20[0-4]\d)\b")


@dataclass
class Candidate:
    kind: str  # "platform", "collection" or "record type"
    name: str
    hint: str = ""  # what it usually holds
    covered_by: str | None = None
    urls: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)  # titles, headings, link texts
    files: int = 0
    years: list[int] = field(default_factory=list)

    def summary(self) -> str:
        """The candidate as text, for the model to review (and to check its quotes against)."""
        lines = [f"Candidate ({self.kind}): {self.name}"]
        if self.hint:
            lines.append(f"Usually holds: {self.hint}")
        if self.files:
            lines.append(f"Documents linked: {self.files}")
        if self.years:
            lines.append(f"Years mentioned: {min(self.years)}–{max(self.years)}")
        lines.append("Where:")
        lines += [f"- {u}" for u in self.urls[:8]]
        lines.append("Evidence (page titles, headings and link texts):")
        lines += [f"- {e}" for e in dict.fromkeys(self.evidence)][:40]
        return "\n".join(lines)


def platforms(pages: list[Page]) -> list[Candidate]:
    found: dict[str, Candidate] = {}
    for page in pages:
        for link in [*page.external, *page.links, *page.files]:
            for name, (pattern, hint, covered) in PLATFORMS.items():
                if re.search(pattern, link.url, re.I):
                    c = found.setdefault(name, Candidate("platform", name, hint, covered))
                    if link.url not in c.urls:
                        c.urls.append(link.url)
                    c.evidence += [x for x in (link.text, f"linked from: {page.title}") if x]
    return list(found.values())


def collections(pages: list[Page]) -> list[Candidate]:
    out = []
    for page in pages:
        if len(page.files) < DOCS_FOR_A_COLLECTION:
            continue
        name = page.headings[0] if page.headings else page.title or page.url
        texts = [f.text or f.url.rsplit("/", 1)[-1] for f in page.files]
        years = sorted({int(y) for t in texts for y in YEAR.findall(t)})
        out.append(Candidate("collection", name, urls=[page.url], files=len(page.files),
                             years=years, evidence=[page.title, *page.headings[:5],
                                                    *texts[:25]]))  # fmt: skip
    return out


def record_types(pages: list[Page]) -> list[Candidate]:
    found: dict[str, Candidate] = defaultdict(lambda: Candidate("record type", ""))
    for page in pages:
        texts = [page.title, *page.headings, *(link.text for link in page.links + page.files)]
        for name, pattern in RECORD_TYPES.items():
            hits = [t for t in texts if t and re.search(pattern, t, re.I)]
            if not hits:
                continue
            c = found[name]
            c.name = name
            if page.url not in c.urls:
                c.urls.append(page.url)
            c.evidence += hits[:6]
            c.files += sum(1 for f in page.files if f.text and re.search(pattern, f.text, re.I))
    return [c for c in found.values() if len(c.urls) >= 2 or c.files]


def candidates(pages: list[Page]) -> list[Candidate]:
    return [*platforms(pages), *collections(pages), *record_types(pages)]


PROSE = re.compile(
    r"\b(departments?|public records|open records|records requests?|foia|"
    r"transparency|documents|finance|clerk|auditor|permits?|forms)\b",
    re.I,
)


def prose_pages(pages: list[Page], limit: int = 8) -> list[Page]:
    """The pages most likely to describe the government's records in prose."""
    scored = [(len(PROSE.findall(f"{p.title} {' '.join(p.headings)} {p.url}")), p)
              for p in pages if len(p.text) > 200]  # fmt: skip
    return [p for score, p in sorted(scored, key=lambda x: (-x[0], x[1].depth)) if score][:limit]
