"""The licensing boards the Department of Health hosts, and the list each publishes of the
licenses it has issued.

A list is found on its board's page each run (its file name changes when the board posts a
new one: "active-licensees-as-of-10-13-26.pdf"), by the pattern of its link. Kinds:

- roster: a PDF the boards' shared licensing portal (sdboards.org) generates on request, a
  table under a header.
- pdf: a PDF the board posts, a table under a header.
- midwives: the midwives board's PDF, a block of text a licensee.
- csv: a board's licensee search app (*.appssd.sd.gov/Licensees), whose Download button
  gives the whole list as CSV. The app needs a browser.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

DOH = "https://doh.sd.gov/licensing-and-records/boards/"
PORTAL = "https://www.sdboards.org/healthdept/"

# The portal's rosters: Last Name | First Name | Level | License # | Effective Date |
# Expiration Date | Discipline
ROSTER = {"Last": "last_name", "First": "first_name", "Level": "license_type",
          "License": "license_number", "Effective": "issued", "Expiration": "expires",
          "Discipline": "discipline"}  # fmt: skip


@dataclass(frozen=True)
class List:
    slug: str
    board: str
    profession: str  # who the list's licenses are for
    kind: str  # roster, pdf, midwives, csv
    page: str  # the board's page that links the list (csv: the app itself)
    link: str = ""  # a pattern its link matches
    columns: dict | tuple = field(default_factory=dict)  # header word → field (tables.read)
    holder: str = "person"  # or "establishment"
    license_type: str | None = None  # when the list doesn't say: every license on it
    by_type: bool = False  # numbers repeat across license types: key on both
    ids: str = ""  # a pattern every license number in the list's text matches (the check)


LISTS = [
    List("certified-professional-midwives", "Board of Certified Professional Midwives",
         "Certified professional midwives and student midwives", "midwives",
         DOH + "certified-professional-midwives/", r"licensed-midwives[^\"']*\.pdf"),
    List("chiropractic", "Board of Chiropractic Examiners", "Chiropractors", "csv",
         "https://bocelicensing.appssd.sd.gov/Licensees"),
    List("optometry", "Board of Examiners in Optometry", "Optometrists", "csv",
         "https://optometry.appssd.sd.gov/Licensees"),
    List("speech-language-pathology", "Board of Examiners for Speech-Language Pathology",
         "Speech-language pathologists and assistants", "roster",
         PORTAL + "SpeechPath/verify/", r"roster\.pdf", ROSTER,
         ids=r"\b\d+-(?:SLPA?|LTD|PROV)\b"),
    List("funeral-directors", "Board of Funeral Service", "Funeral directors and embalmers",
         "roster", PORTAL + "funeral/embalmer/verify/", r"roster\.pdf", ROSTER),
    List("funeral-establishments", "Board of Funeral Service", "Funeral establishments",
         "roster", PORTAL + "funeral/verify/", r"homeroster\.pdf",
         {"License": "license_number", "Funeral": "name", "City": "city",
          "Expiration": "expires"}, holder="establishment",
         license_type="Funeral establishment"),
    List("crematories", "Board of Funeral Service", "Crematories", "roster",
         PORTAL + "funeral/verify/", r"crematoryroster\.pdf",
         {"License": "license_number", "Type": "license_type", "Funeral": "name",
          "City": "city", "Expiration": "expires"}, holder="establishment"),
    List("hearing-aid-dispensers-audiologists",
         "Board of Hearing Aid Dispensers and Audiologists",
         "Hearing aid dispensers and audiologists", "roster", PORTAL + "audiology/verify/",
         r"roster\.pdf", ROSTER, ids=r"\b\d+-[A-Z]\b"),
    List("massage-therapy", "Board of Massage Therapy", "Massage therapists", "pdf",
         DOH + "massage/license-verification/", r"active-licensees[^\"']*\.pdf",
         {"License": "license_number", "First": "first_name", "Last": "last_name",
          "Expiration": "expires", "Original": "issued", "City": "city", "State": "state"},
         license_type="Massage therapist", ids=r"\bMT\d{4,}\b"),
    List("massage-therapy-temporary", "Board of Massage Therapy",
         "Massage therapists (temporary permits)", "pdf",
         DOH + "massage/license-verification/", r"active-temporary-permits[^\"']*\.pdf",
         {"Last": "last_name", "First": "first_name", "Permit": "license_number",
          "Issued": "issued", "Expiration": "expires", "City": "city", "State": "state"},
         license_type="Temporary permit", ids=r"\bTEMP\d+\b"),
    List("nursing-facility-administrators", "Board of Nursing Facility Administrators",
         "Nursing facility administrators", "pdf",
         DOH + "nursing-facility-administrators/", r"nfa-licensees[^\"']*\.pdf",
         (("First", "first_name"), ("Last", "last_name"), ("Facility", "facility_city"),
          ("Facility", "facility_state"), ("Facility", "facility_phone"),
          ("Original", "issued"), ("License", "expires"), ("License", "license_number"),
          ("Preceptor", "preceptor_through")),
         license_type="Nursing facility administrator"),
    List("physical-therapy", "Board of Physical Therapy",
         "Physical therapists and assistants", "roster", PORTAL + "physicaltherapy/verify/",
         r"roster\.pdf", ROSTER, by_type=True),
    List("podiatry", "Board of Podiatry Examiners", "Podiatrists", "roster",
         PORTAL + "podiatry/verify/", r"roster\.pdf", ROSTER),
]  # fmt: skip
BY_SLUG = {x.slug: x for x in LISTS}


class NotLinked(Exception):
    """The board's page no longer links its list."""


def find(html: str, base: str, pattern: str) -> str:
    """The list's address on its board's page: the first link matching pattern, without the
    portal's cache-busting query ("roster.pdf?tim=1794868279")."""
    from urllib.parse import urljoin

    for href in re.findall(r"""href=["']([^"']+)["']""", html):
        if re.search(pattern, href, re.I):
            return urljoin(base, href.split("?")[0].split("#")[0])
    raise NotLinked(f"no link like {pattern!r} on {base}")
