"""Each kind of list as licenses (schema.License fields): tables by their header (tables.py),
the midwives' text, and the licensee apps' CSV."""

from __future__ import annotations

import csv
import io
import re

from sddocs.clean import parse_date

from . import tables
from .boards import List

FOOTER = re.compile(r"^(Page \d+|Last updated)", re.I)
NO_DISCIPLINE = {"no", "none", "false", "n"}
FIELDS = {"license_number", "license_type", "name", "last_name", "first_name", "city",
          "state", "status", "issued", "expires", "discipline"}  # fmt: skip


def _date(text: str | None) -> str | None:
    on = parse_date(text) if text else None
    return on.isoformat() if on else None


def _disciplined(text: str | None) -> bool | None:
    if not text or not text.strip():
        return None
    return text.strip().lower() not in NO_DISCIPLINE


def license(lst: List, row: dict) -> dict:
    """A row (field → text, as tables.read or the CSV gives it) as a License's fields."""
    row = {k: (v.strip() if isinstance(v, str) else v) for k, v in row.items()
           if not k.startswith("_")}  # fmt: skip
    first, last = row.get("first_name") or None, row.get("last_name") or None
    name = row.get("name") or " ".join(x for x in (first, last) if x)
    issued, expires = row.get("issued") or None, row.get("expires") or None
    details = {k: v for k, v in row.items() if k not in FIELDS and v not in (None, "")}
    details |= row.get("details") or {}
    details.pop("details", None)
    return {
        "list": lst.slug, "board": lst.board, "profession": lst.profession,
        "holder": lst.holder, "license_number": row["license_number"],
        "license_type": row.get("license_type") or lst.license_type, "name": name,
        "last_name": last, "first_name": first, "city": row.get("city") or None,
        "state": row.get("state") or None, "status": row.get("status") or None,
        "issued": _date(issued), "issued_as_listed": issued, "expires": _date(expires),
        "expires_as_listed": expires, "discipline": row.get("discipline") or None,
        "disciplined": _disciplined(row.get("discipline")), "details": details,
    }  # fmt: skip


def table(lst: List, pdf) -> tuple[list[dict], list[str]]:
    """A roster or posted PDF's licenses, and the lines that fit no row (strays)."""
    t = tables.read(pdf, lst.columns, anchor="license_number", skip=FOOTER)
    return [license(lst, r) for r in t.rows], t.strays


# --- the midwives' list: a block of text a licensee ---------------------------------------

HEAD = re.compile(r"^(?P<name>.+?) (?P<credential>CPM|SM) License #\s?(?P<number>\d+S?)\s*"
                  r"(?:Issued (?P<issued>.+)|(?P<status>Lapsed|Inactive Status),\s*"
                  r"(?P<on>.+))$")  # fmt: skip
CREDENTIAL = {"CPM": "Certified professional midwife", "SM": "Student midwife"}
LOCATION = re.compile(r"^(?P<city>.*?),?\s+(?P<state>[A-Z]{2})(?:\s+\d{5})?$")


def midwives(text: str) -> list[dict]:
    """The midwives' list: "Eileen Carlson CPM License #001901 Issued March 1, 2019", then
    "Expires …", "Location: …", "Preceptor: …" and contact lines. Licenses under "Inactive
    Licenses" give their status and its date instead of an issue date."""
    out, current, key = [], None, None
    for line in (x.strip() for x in text.splitlines()):
        if not line or line.startswith("South Dakota Licensed Certified"):
            continue
        m = HEAD.match(line)
        if m:
            current = {"license_number": m["number"], "name": m["name"].strip(),
                       "license_type": CREDENTIAL[m["credential"]],
                       "issued": m["issued"], "status": m["status"],
                       "details": {"listing": [line]}}  # fmt: skip
            if m["on"]:
                current["details"]["status_date"] = _date(m["on"])
                current["details"]["status_date_as_listed"] = m["on"].strip()
            out.append(current)
            key = None
            continue
        if current is None or line in ("Licensed Student Midwives", "Inactive Licenses"):
            continue
        current["details"]["listing"].append(line)
        if line.startswith("Expires"):
            current["expires"] = line.removeprefix("Expires").strip(" :")
        elif line.startswith("Location:"):
            where = line.removeprefix("Location:").strip()
            loc = LOCATION.match(where)
            current["details"]["location"] = where
            if loc:
                current["city"], current["state"] = loc["city"].strip(" ,"), loc["state"]
        elif re.match(r"^Preceptors?:", line):
            key = "preceptor"
            current["details"]["preceptor"] = line.split(":", 1)[1].strip()
        elif key == "preceptor" and re.search(r"\b(CPM|CNM)\b", line):
            current["details"]["preceptor"] += "; " + line  # a second preceptor, own line
        else:
            key = None
    for row in out:
        row["details"]["listing"] = "\n".join(row["details"]["listing"])
    return out


# --- the licensee apps' CSV -------------------------------------------------------------

CSV_FIELDS = {
    # chiropractic: Name, Title, LicenseNumber, Effective, Status, Expiration, GoodStanding,
    # DisciplinaryAction, Acupuncture (and *Icon, TotalPageCount: the app's display)
    "Name": "name", "Title": "license_type", "LicenseNumber": "license_number",
    "Effective": "issued", "Issued": "issued", "Status": "status", "Expiration": "expires",
    "DisciplinaryAction": "discipline", "Discipline": "discipline",
    # optometry: LastName, FirstName, …, Type, Address, CityStateZip, Phone, AdvancedProcedures
    "LastName": "last_name", "FirstName": "first_name", "Type": "license_type",
}  # fmt: skip
CITY_STATE_ZIP = re.compile(r"^(?P<city>[^,]*?)\s*,\s*(?P<state>[A-Z]{2})\b")


def csv_rows(lst: List, data: bytes) -> list[dict]:
    out = []
    for raw in csv.DictReader(io.StringIO(data.decode("utf-8-sig"))):
        row: dict = {"details": {}}
        for col, value in raw.items():
            if col is None or col.endswith("Icon") or col == "TotalPageCount":
                continue
            value = (value or "").strip()
            if value.endswith(" 12:00:00 AM"):
                value = value.removesuffix(" 12:00:00 AM")
            if col in CSV_FIELDS:
                row[CSV_FIELDS[col]] = value
            elif value:
                row["details"][col] = value
        where = CITY_STATE_ZIP.match(row["details"].get("CityStateZip", ""))
        if where:
            row["city"], row["state"] = where["city"].strip() or None, where["state"]
        out.append(license(lst, row))
    return out
