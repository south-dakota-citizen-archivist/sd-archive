"""Legislators' biographies: every bio page the Legislature's Historical Listing gives for a
member, one record per page. The Blue Book (Legislative Manual) chapters on the Legislature,
1991–2023, and the Biographical Directory (two volumes and errata) for earlier legislators.

Built from the legislature crawl's stored responses (Historical/Member/<id>) and the page
images kept beside them (`manage.py fetch_bio_images`, `ocr_bio_images`); nothing is
fetched here. Each page has two readings: the legislature's own OCR (the API's ParsedText)
and ours (rapidocr on the image). Blue Book pages are read into parts (party, district,
counties, address, birth, spouse, children, email, committees, occupation, …; see parse.py)
from both readings, and a part's value is kept only where the two readings agree. Where they
don't, the page is read a third time (Apple Vision) and a part two of the three agree on is
kept and listed in `two_of_three`. Otherwise the readings are kept under `readings` and the
part is listed in `unconfirmed`. Before comparing, each reading is cut to the legislator's own
bio (parse.own_bio): an image can take in the end of the bio above or the start of the one
below. The Biographical Directory's prose is kept as text.

Party and counties are checked against the member's services in the Historical Listing for
the book's year (`listing_party`, `party_agrees`). Records link to the member's profile
page on sdlegislature.gov, which shows the same images.
"""

from __future__ import annotations

import json
import re
from difflib import SequenceMatcher

import sdplaces
from entities.mentions import from_text
from records.sources import Found, Item, Source
from records.sources._legislature.build import SITE, raw_root

from . import parse
from .images import image_id, image_name
from .ocr import TIEBREAK, ocr_name

STATE = "0400000US46"
PARTS = ("party", "district", "counties", "address", "birth", "spouse", "children", "email",
         "religion", "education", "experience", "legislative_experience", "occupation",
         "committees")  # fmt: skip
AGREE = 0.9  # similarity of two readings' letters and digits, for a part to be kept
COUNTY_NAMES = None


def kind_of(title: str) -> str:
    if "Blue Book" in title:
        return "Blue Book"
    return "Biographical Directory errata" if "Errata" in title else "Biographical Directory"


def _letters(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def agree(a: str | None, b: str | None) -> bool:
    """Two readings of a part agree: the same digits, in order (a house number, a date, a
    district, never close enough), and the letters nearly the same (OCR's small slips)."""
    if not a or not b:
        return False
    if re.findall(r"\d", a) != re.findall(r"\d", b):
        return False
    a, b = _letters(a), _letters(b)
    return a == b or SequenceMatcher(None, a, b).ratio() >= AGREE


def county_places(counties: str | None, year: int | None) -> list[str]:
    """The counties named in a bio's district line ("Haakon, Jackson … and part of Lyman
    counties"), as of the book's year."""
    global COUNTY_NAMES
    if not counties:
        return []
    if COUNTY_NAMES is None:
        COUNTY_NAMES = sorted({c.name.removesuffix(" County")
                               for c in sdplaces.counties(include_historical=True)},
                              key=len, reverse=True)  # fmt: skip
    found, text = [], counties
    for name in COUNTY_NAMES:
        if re.search(rf"\b{re.escape(name)}\b", text):
            text = re.sub(rf"\b{re.escape(name)}\b", " ", text)
            try:
                place = sdplaces.county(name, as_of=f"{year}-06-30" if year else None)
            except (sdplaces.UnknownPlace, sdplaces.AmbiguousPlace):
                continue
            found.append(place.geoidfq)
    return sorted(set(found))


def listing_party(member: dict, year: int | None) -> str | None:
    """The member's party in the Historical Listing for a service that includes the year."""
    if not year:
        return None
    for s in member.get("MemberServices") or []:
        start, end = s.get("StartYear"), s.get("EndYear")
        if start and start <= year and (end is None or year <= end + 1):
            return s.get("Party")
    return None


def page_item(member: dict, picture: dict, store) -> Item:
    mid, title = member["MemberId"], picture["Title"]
    year = int(title[:4]) if title[:4].isdigit() else None
    image = image_id(picture)
    ours = json.loads(store.read(ocr_name(image))) if store.has(ocr_name(image)) else None
    data = {
        "member_id": mid,
        "member": " ".join(f"{member.get('FirstName') or ''} {member.get('LastName') or ''}"
                           .split()),  # fmt: skip
        "book": title, "kind": kind_of(title), "year": year,
        "page": picture.get("PageNumber"), "book_document_id": picture.get("DocumentId"),
        "image_id": image, "image_stored": store.has(image_name(picture)),
        "text": parse.clean_text(picture.get("ParsedText") or ""),
        "ocr_text": parse.clean_text(ours["text"]) if ours else None,
        "ocr_engine": ours["engine"] if ours else None,
    }  # fmt: skip
    if data["kind"] == "Blue Book":
        last = (member.get("LastName") or "").split()[-1:] or [None]
        tiebreak = (json.loads(store.read(ocr_name(image, TIEBREAK)))
                    if store.has(ocr_name(image, TIEBREAK)) else None)  # fmt: skip
        first = parse.read(picture.get("ParsedText") or "", last[0])
        second = parse.read(ours["text"], last[0]) if ours else {}
        third = parse.read(tiebreak["text"], last[0]) if tiebreak else None
        readings, unconfirmed, two_of_three = {}, [], []
        for part in PARTS:
            a, b = first.get(part), second.get(part)
            c = third.get(part) if third else None
            if agree(a, b):
                data[part] = b
            elif third and (agree(b, c) or agree(a, c)):
                data[part] = b if agree(b, c) else a  # the two that agree
                two_of_three.append(part)
            else:
                data[part] = None
                if a or b or c:
                    readings[part] = {"legislature": a, "ours": b}
                    if third:
                        readings[part]["vision"] = c
                    unconfirmed.append(part)
        data["name_as_printed"] = second.get("name") or first.get("name")
        data["readings"], data["unconfirmed"] = readings, unconfirmed
        if two_of_three:  # settled by the third reading; every other kept part, by two
            data["two_of_three"] = two_of_three
        data["listing_party"] = listing_party(member, year)
        data["party_agrees"] = (data["party"] == data["listing_party"]
                                if data["party"] and data["listing_party"] else None)  # fmt: skip
    places = [STATE, *county_places(data.get("counties"), year)]
    return Item(key=image, data=data, places=places,
                source_url=f"{SITE}Historical/Legislator/Profile/{mid}")  # fmt: skip


def items(fetcher=None) -> Found:
    from sdfetch import Store

    store = Store(raw_root())
    out = Found()
    counts = {"pages": 0, "without_image": 0, "without_our_reading": 0, "party_disagrees": []}
    for path in sorted((raw_root() / "api" / "Historical" / "Member").glob("*.json"),
                       key=lambda p: int(p.stem)):  # fmt: skip
        member = json.loads(path.read_text() or "{}")
        for picture in member.get("Pictures") or []:
            item = page_item(member, picture, store)
            out.append(item)
            counts["pages"] += 1
            counts["without_image"] += not item.data["image_stored"]
            counts["without_our_reading"] += item.data["ocr_text"] is None
            if item.data.get("party_agrees") is False:
                counts["party_disagrees"].append(item.key)
    counts["party_disagrees"] = counts["party_disagrees"][:50]
    out.checks = {k: v for k, v in counts.items() if v}
    return out


def mentions(record) -> list:
    """The legislator (matched to the Historical Listing's member), and a spouse named in
    full where both readings agree."""
    d = record.data
    out = from_text(d["member"], dataset=record.recordset.dataset, record_key=str(record),
                    field="member", role="legislator", order="given_first",
                    url=record.source_url, gold_entity_id=f"sd-legislator:{d['member_id']}",
                    context={"book": d["book"], "page": d["page"]})  # fmt: skip
    spouse = d.get("spouse")
    if spouse:
        name = spouse.split(",")[0].strip()  # "Beverly Johnson, June 21, 1953, Vermillion"
        if len(name.split()) >= 2 and re.fullmatch(r"[A-Z][\w.'\- ]+", name):
            out += from_text(name, dataset=record.recordset.dataset, record_key=str(record),
                             field="spouse", role="spouse of a legislator",
                             order="given_first", url=record.source_url, start=len(out),
                             context={"legislator": d["member"], "book": d["book"]})  # fmt: skip
    return out


SOURCE = Source(
    slug="legislature-blue-book-bios", items=items, mentions=mentions,
    dataset="legislature", publisher="South Dakota Legislature",
    name="Legislators' biographies", base_url="https://sdlegislature.gov/", max_drop=0.05,
    description="Every legislator biography page in the Legislature's Historical Listing: "
                "the Blue Book chapters on the Legislature (1991–2023) and the Biographical "
                "Directory, with each Blue Book page read into parts (party, district, "
                "counties, address, birth, spouse, children, committees) where two "
                "readings agree.",
)  # fmt: skip
