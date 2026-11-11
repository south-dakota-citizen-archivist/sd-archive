"""The Secretary of State's lobbyist registrations (sosenterprise.sd.gov), shared by the
private and public lobbyist record sets.

- The search form posts back over HTTP. Its "Printer Friendly Version" is a PDF of every
  registration (all years), read here by its table's column positions: the list a run is
  checked against.
- A search shows at most 1,000 results, but a search by last name shows every year, so the
  private set searches each surname, collects each registration's detail page (its CN is the
  stable key) and reads the page. The site's "Export Selected Year" is only a subset.
"""

from __future__ import annotations

import io
import itertools
import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlsplit

from records.sources._sdcfr import form_fields
from records.sources._tables import LayoutChanged, document, text

SITE = "https://sosenterprise.sd.gov/BusinessServices/Lobbyist/"
SEARCH = SITE + "LobbyistSearch.aspx"
DETAIL = SITE + "LobbyistRegistrationDetail.aspx"
FILINGS = "https://sosenterprise.sd.gov/BusinessServices/Business/"
FIXES = json.loads((Path(__file__).parent / "_lobbyist_fixes.json").read_text())

# The printer-friendly PDFs' columns: x ranges in points (the last runs to the page edge)
COLUMNS = {
    "private": {"year": (0, 70), "expense_report_lobbyist": (70, 122),
                "expense_report_employer": (122, 180), "lobbyist_name": (180, 343),
                "employer": (343, 500), "status": (500, None)},
    "public": {"year": (0, 75), "lobbyist_name": (75, 305), "agency": (305, None)},
}  # fmt: skip


_CITY_LINE = re.compile(
    r"^(?P<city>[^,]*?)\s*,\s*(?P<state>[A-Z]{2})?\s*(?P<zip>\d{5}(?:-\d{4})?)?$"
)


def _tidy(line: str) -> str:
    """Repeated commas and states: "SIOUX FALLS, , SD 57105", "MILBANK, SD, SD 57252",
    "PIERRE, SD 57501, SD 57501"."""
    line = " ".join(re.sub(r"(?:\s*,)+", ", ", line).split()).rstrip(",")
    line = re.sub(r", ([A-Z]{2}) (\d{5}), \1 \2$", r", \1 \2", line)
    return re.sub(r", ([A-Z]{2}), \1\b", r", \1", line)


def address_parts(lines: list[str], prefix: str) -> dict:
    """An address's lines as written (street lines, then "CITY, ST ZIP", then the country)
    split into {prefix}_street, _city, _state, _zip and _country; parts that are missing
    are None."""
    lines = [_tidy(line) for line in lines]
    lines = [x for x in lines if x and x != "-"]
    parts = dict.fromkeys(("street", "city", "state", "zip", "country"))
    if len(lines) > 1 and not _CITY_LINE.match(lines[-1]) and not re.search(r"\d", lines[-1]):
        parts["country"] = lines.pop().strip("()")
    if lines and (m := _CITY_LINE.match(lines[-1])):
        lines.pop()
        parts |= {k: v or None for k, v in m.groupdict().items()}
    parts["street"] = " ".join(lines) or None
    return {f"{prefix}_{k}": v for k, v in parts.items()}


class Session:
    """The search form, posted back as a browser does."""

    def __init__(self, fetcher):
        self.fetcher = fetcher
        self.page = fetcher.request("GET", SEARCH).content

    def _post(self, target: str, **fields) -> bytes:
        data = {**form_fields(self.page), "__EVENTTARGET": target, "__EVENTARGUMENT": "",
                **fields}  # fmt: skip
        return self.fetcher.request("POST", SEARCH, data=data, timeout=300).content

    def choose(self, kind: str) -> None:
        """Private (the default) or public lobbyists."""
        if kind == "public":
            choice = {"ctl00$MainContent$searchBy": "chkSearchByPublic"}
            self.page = self._post("ctl00$MainContent$chkSearchByPublic", **choice)

    def search(self, last_name: str = "", year: str = "0") -> bytes:
        fields = {"ctl00$MainContent$txtLastName": last_name,
                  "ctl00$MainContent$slctYears": year}  # fmt: skip
        self.page = self._post("ctl00$MainContent$SearchButton", **fields)
        return self.page

    def printable(self) -> bytes:
        """The printer-friendly PDF of the current search."""
        pdf = self._post("ctl00$MainContent$PrintButton",
                         **{"ctl00$MainContent$slctYears": "0"})  # fmt: skip
        if not pdf.startswith(b"%PDF"):
            raise LayoutChanged("the printer-friendly version isn't a PDF")
        return pdf


# --- the printer-friendly PDF ---


def _record_crops(page):
    """A page cut into one crop per registration: the table's rows are separated by gray
    rectangles."""
    breaks = sorted([page.bbox[1], page.bbox[3], *(r["top"] for r in page.rects),
                     *(r["bottom"] for r in page.rects)])  # fmt: skip
    crops = []
    for top, bottom in itertools.pairwise(breaks):
        if bottom - top < 1:
            continue
        crop = page.crop((0, top, page.width, bottom))
        if crop.extract_text():
            crops.append(crop)
    return crops


def _cell_lines(crop, x0: float, x1: float | None) -> list[str]:
    cell = crop.crop((x0, crop.bbox[1], x1 if x1 is not None else crop.width, crop.bbox[3]))
    return [" ".join(x.split()) for x in cell.extract_text(layout=True).upper().splitlines()
            if x.strip()]  # fmt: skip


def pdf_rows(pdf_bytes: bytes, kind: str) -> list[dict]:
    """Every registration in a printer-friendly PDF, as written (upper case)."""
    import pdfplumber

    out = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            if page.page_number == 1:  # the search's description sits above the table
                page = page.crop((0, page.lines[0]["bottom"] + 1, page.width, page.height))
            for crop in _record_crops(page):
                row = {}
                for field, (x0, x1) in COLUMNS[kind].items():
                    lines = _cell_lines(crop, x0, x1)
                    if field in ("lobbyist_name", "agency"):
                        row[field] = lines[0] if lines else ""
                        row[f"{field}_address"] = " ".join(lines[1:])
                        if field == "agency":
                            row |= address_parts(lines[1:], "agency_address")
                    else:
                        row[field] = " ".join(lines)
                if kind == "private":
                    name = row["lobbyist_name"]
                    row["lobbyist_name"] = FIXES["name_fixes"].get(name, name)
                if row.get("year", "").isdigit() and not row["lobbyist_name"].startswith("TEST "):
                    out.append(row)
    return out


# --- search results and detail pages ---

RESULT_COLUMNS = ["year", "registration_number", "status", "lobbyist_name", "lobbyist_city",
                  "lobbyist_contact", "employer", "employer_address", "employer_city"]  # fmt: skip


def result_rows(html: bytes) -> list[dict]:
    """The registrations a search found, each with its detail page URL."""
    tables = document(html).cssselect("table#DataTables_Table_0, table.table")
    if not tables:
        return []
    out = []
    for tr in tables[0].cssselect("tr")[1:]:
        cells = tr.findall("td")
        if len(cells) != len(RESULT_COLUMNS):
            continue
        link = cells[1].find(".//a[@href]")
        row = dict(zip(RESULT_COLUMNS, (text(c) for c in cells), strict=True))
        row["url"] = urljoin(SEARCH, link.get("href")) if link is not None else None
        out.append(row)
    return out


def cn(url: str) -> str:
    return parse_qs(urlsplit(url).query)["CN"][0]


DETAIL_SPANS = {
    "lobbyist_name": "txtLobbyistName", "lobbyist_status": "txtStatus",
    "lobbyist_employment_date": "txtEmploymentDate", "lobbyist_phone": "txtPhone",
    "lobbyist_email": "txtEmail", "lobbyist_address": "txtResidenceAddress",
    "lobbyist_occupation": "txtOccupation", "lobbyist_type": "txtType",
    "employer_name": "txtEmployerName", "employer_agent_name": "txtAgentName",
    "employer_registration_date": "txtRegistrationDate",
    "employer_authorization_date": "txtAuthorizationDate",
    "employer_lobbying_subjects": "txtSubject",
    "employer_registration_status": "txtRegistrationStatus",
    "employer_address": "txtEmployerAddress",
}  # fmt: skip


def _date(value: str) -> str | None:
    try:
        return datetime.strptime(value, "%m/%d/%Y").date().isoformat()
    except ValueError:
        return None


def detail(html: bytes, url: str) -> dict:
    """A registration's detail page: the lobbyist, the employer, and the filings."""
    root = document(html)
    number = root.cssselect("#ctl00_MainContent_lblRegistrationNo")
    if not number:
        raise LayoutChanged(f"{url}: no registration number")
    year, _, rest = text(number[0]).partition("-")
    guid = cn(url)
    d = {"registration_guid": guid, "year": int(year.strip()),
         "registration_number": " ".join(rest.partition(":")[2].split())}  # fmt: skip
    for field, span in DETAIL_SPANS.items():
        found = root.cssselect(f"#ctl00_MainContent_{span}")
        if not found:
            d[field] = None
            continue
        if field.endswith("_address"):
            lines = [t.strip().upper() for t in found[0].itertext() if t.strip()]
            d[field] = " ".join(lines)
            d |= address_parts(lines, field)
        else:
            d[field] = text(found[0]).upper()
        if field.endswith("_date"):
            d[f"{field}_raw"], d[field] = d[field], _date(d[field])
    filings = []
    tables = root.cssselect("table")
    for tr in tables[0].cssselect("tr")[1:] if tables else []:
        cells = tr.findall("td")
        if len(cells) < 3:
            continue
        link = cells[2].find(".//a[@href]")
        f = {"filing_type": text(cells[0]), "filing_date": _date(text(cells[1])),
             "filing_number": text(cells[2]), "filing_url": None, "filing_guid": None}  # fmt: skip
        if link is not None:
            f["filing_url"] = urljoin(FILINGS, Path(link.get("href")).name)
            f["filing_guid"] = parse_qs(urlsplit(f["filing_url"]).query).get("id", [None])[0]
        filings.append(f)
    d["filings"] = filings
    return d
