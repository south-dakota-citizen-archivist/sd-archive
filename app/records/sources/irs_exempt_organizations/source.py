"""Tax-exempt organizations in South Dakota, from the IRS Exempt Organizations Business
Master File extract (its South Dakota file, eo_sd.csv, refreshed monthly): one record per
EIN, with the name, the person the IRS writes to (in care of), the address, and the IRS's
codes (subsection, classification, foundation, NTEE, filing requirement, status) and its
latest asset, income and revenue amounts. Values are kept as published. Each organization is
tied to its city; its name and the in-care-of person are mentioned for matching."""

from __future__ import annotations

import csv
import io

from entities.mentions import from_text
from records.sources import Found, Item, Source
from records.sources._places import city_place

URL = "https://www.irs.gov/pub/irs-soi/eo_sd.csv"
# the IRS shortens city names to fit its field ("N SIOUX CITY", "WESSINGTN SPG")
WORDS = {"N": "NORTH", "S": "SOUTH", "E": "EAST", "W": "WEST", "MT": "MOUNT", "FT": "FORT",
         "SPG": "SPRINGS", "SPGS": "SPRINGS", "WESSINGTN": "WESSINGTON", "CTY": "CITY",
         "VLG": "VILLAGE"}  # fmt: skip
STATE = "0400000US46"


def rows(content: bytes) -> list[dict]:
    text = content.decode("utf-8") if content[:3] != b"\xef\xbb\xbf" else content[3:].decode()
    return list(csv.DictReader(io.StringIO(text)))


def place(city: str) -> str | None:
    """The city's GEOID, trying the IRS's abbreviations spelled out."""
    return city_place(city) or city_place(" ".join(WORDS.get(w, w) for w in city.split()))


def items(fetcher) -> Found:
    content = fetcher.request("GET", URL, timeout=300).content
    try:
        found = rows(content)
    except UnicodeDecodeError:
        found = list(csv.DictReader(io.StringIO(content.decode("cp1252"))))
    out = Found()
    for row in found:
        data = {k.lower(): (v or "").strip() for k, v in row.items()}
        data["in_care_of"] = data["ico"].removeprefix("%").strip() or None
        city = place(data["city"]) if data["state"] == "SD" else None
        out.append(Item(key=data["ein"], data=data, source_url=URL,
                        places=[STATE, *([city] if city else [])]))  # fmt: skip
    out.checks = {"rows": len(found)}
    return out


def mentions(record) -> list:
    d = record.data
    common = {"dataset": record.recordset.dataset, "record_key": str(record),
              "url": record.source_url,
              "context": {"ein": d["ein"], "city": d["city"]}}  # fmt: skip
    out = from_text(d["name"], field="name", role="organization", order="auto", **common)
    if d.get("in_care_of"):
        out += from_text(d["in_care_of"], field="in_care_of", role="in care of",
                         order="given_first", start=len(out), **common)  # fmt: skip
    return out


SOURCE = Source(
    slug="irs-exempt-organizations", items=items, mentions=mentions,
    name="Tax-exempt organizations (IRS)", dataset="irs-exempt-organizations",
    publisher="Internal Revenue Service", base_url=URL,
    description="Every tax-exempt organization the IRS lists in South Dakota, with its address, "
                "in-care-of person, IRS codes, and latest assets, income and revenue.",
)  # fmt: skip
