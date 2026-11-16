"""Polling places, from the Secretary of State's lists for each statewide election (PDF):
county, precinct, polling place, address, city and instructions, 2016 on. Plus the absentee
satellite sites listed for 2018 and 2024.

LISTS is the table of every list, checked by hand: its election (from the elections
catalog, pipelines/elections/data/elections.csv, not the PDF's own title, which is sometimes
wrong: the 2024 primary's says June 8; the primary was June 4), where it is now, and where
it was before. The source also reads the Secretary of State's polling place page for each
election year and reports any list it links that the table doesn't have yet
(lists_not_in_the_table), so a new election's list gets added there rather than guessed at.

One record per line of a list, keyed by election, county, precinct and polling place (vote
center counties list a precinct once per vote center; a line printed twice gets a numbered
key). Each record is tied to the state, its county as of the election and its city.
Lists for elections still to come, or within a month after, are fetched again each run;
older lists are read from the stored copy.

The same building can appear under different names and addresses from one list to the next
(the Huron Arena and the Huron Event Center are one complex), so polling places aren't
matched across elections here.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from urllib.parse import quote, unquote, urljoin

import sdplaces
from entities.mentions import from_text
from records.sources import Found, Item, Source, _sos_search
from records.sources._places import city_place

from . import parse

logger = logging.getLogger(__name__)

STATE = "0400000US46"
SOS = "https://sdsos.gov/elections-voting"
ASSETS = f"{SOS}/assets"
CURRENT = f"{SOS}/upcoming-elections/general-information"
UPCOMING_PAGE = CURRENT + "/{year}-Election-Polling-Places.aspx"
HISTORY_PAGE = (
    f"{SOS}/election-resources/election-history/{{year}}/{{year}}-Election-Polling-Places.aspx"
)
POLLING = "polling places"
SATELLITE = "absentee satellite sites"
REFETCH_AFTER = timedelta(days=31)  # a list can still change until a month after its election
LIST_LINK = re.compile(r"poll|satellite", re.I)
NOT_A_LIST = re.compile(r"key|watcher|manual|guide|checklist|epollbook|percounty", re.I)


@dataclass(frozen=True)
class PollingList:
    on: str  # the election's date (for a satellite list, the year's general election)
    kind: str  # POLLING or SATELLITE
    election: str
    eid: int | None  # the election's ID in the elections catalog
    url: str
    earlier: tuple[str, ...] = field(default=())  # where it was published before

    @property
    def slug(self) -> str:
        if self.kind == SATELLITE:
            return f"{self.on[:4]}-absentee-satellite-sites"
        return f"{self.on}-{self.election.split()[0].lower()}"


def _a(path: str) -> str:
    return f"{ASSETS}/{quote(path)}"


def _c(path: str) -> str:
    return f"{CURRENT}/{quote(path)}"


LISTS = [
    PollingList("2016-11-08", POLLING, "General Election", 178,
                _a("Archive/2016 Assets/2016GeneralElectionPollingPlaces_SouthDakota.pdf"),
                (_a("2016 Assets/2016GeneralElectionPollingPlaces_SouthDakota.pdf"),
                 _a("2016GeneralElectionPollingPlaces_SouthDakota.pdf"))),
    PollingList("2018-06-05", POLLING, "Primary Election", 290,
                _a("Archive/2018 Assets/2018PrimaryPrecinctsandPollingPlaces.pdf"),
                (_a("2018 Assets/2018PrimaryPrecinctsandPollingPlaces.pdf"),
                 _a("2018PrimaryPrecinctsandPollingPlaces.pdf"))),
    PollingList("2018-11-06", SATELLITE, "2018 primary and general elections", None,
                _a("Archive/2018 Assets/2018AbsenteeSatelliteSiteswithAddresses.pdf"),
                (_a("2018 Assets/2018AbsenteeSatelliteSiteswithAddresses.pdf"),
                 _a("2018AbsenteeSatelliteSiteswithAddresses.pdf"))),
    PollingList("2018-11-06", POLLING, "General Election", 291,
                _a("Archive/2018 Assets/2018GeneralElectionPrecinctandPollingPlaces.pdf"),
                (_a("2018 Assets/2018GeneralElectionPrecinctandPollingPlaces.pdf"),
                 _a("2018GeneralElectionPrecinctandPollingPlaces.pdf"))),
    PollingList("2020-06-02", POLLING, "Primary Election", 362,
                _a("Archive/Prior to 2026/PrecinctPollingPlaces-2020 Primary.pdf"),
                (_a("PrecinctPollingPlaces-2020 Primary.pdf"),)),
    PollingList("2020-11-03", POLLING, "General Election", 422,
                _a("Archive/2020 Assests/2020GeneralPollingLocations.pdf"),
                (_a("2020 Assests/2020GeneralPollingLocations.pdf"),
                 _a("2020GeneralPollingLocations.pdf"))),
    PollingList("2022-06-07", POLLING, "Primary Election", 470,
                _a("Archive/Prior to 2026/pollingplaces.pdf"), (_a("pollingplaces.PDF"),)),
    PollingList("2022-11-08", POLLING, "General Election", 471,
                _a("Archive/Prior to 2026/pollingplacesg22.pdf"), (_a("pollingplacesg22.PDF"),)),
    PollingList("2024-06-04", POLLING, "Primary Election", 651,
                _a("Archive/2024 Assets/2024PrimaryPollingLocations.pdf"),
                (_a("2024 Assets/2024PrimaryPollingLocations.pdf"),
                 _a("2024PrimaryPollingLocations.pdf"))),
    PollingList("2024-11-05", SATELLITE, "2024 primary and general elections", None,
                _a("Archive/2024 Assets/2024AbsenteeSatelliteSites.pdf"),
                (_a("2024 Assets/2024AbsenteeSatelliteSites.pdf"),
                 _a("2024AbsenteeSatelliteSites.pdf"))),
    PollingList("2024-11-05", POLLING, "General Election", 684,
                _a("Archive/2024 Assets/2024GeneralPollingLocation.pdf"),
                (_a("2024 Assets/2024GeneralPollingLocation.pdf"),)),
    PollingList("2026-06-02", POLLING, "Primary Election", 773,
                _c("2026 Election Assets/Primary/2026 Primary Polling Places.pdf")),
    PollingList("2026-07-28", POLLING, "Runoff Election", 801,
                _c("2026 Election Assets/Runoff/PrecinctPollingPlaces for Runoff.pdf")),
    PollingList("2026-11-03", POLLING, "General Election", 774,
                _c("2026 Election Assets/General/PrecinctPollingPlaces_10.27.26.pdf")),
]  # fmt: skip


def same_url(a: str, b: str) -> bool:
    return unquote(a).lower().replace("http://", "https://") == unquote(b).lower().replace(
        "http://", "https://")  # fmt: skip


def known(url: str) -> PollingList | None:
    return next((p for p in LISTS if any(same_url(url, u) for u in (p.url, *p.earlier))), None)


def _get(fetcher, url: str, name: str | None = None, refresh: bool = False) -> bytes:
    """A request to sdsos.gov, paced like every other; stored at name when given."""
    if name and not refresh and fetcher.store is not None and fetcher.store.has(name):
        return fetcher.store.read(name)
    _sos_search._pace()
    if name and fetcher.store is not None:
        return fetcher.fetch(url, name, refresh=refresh)
    return fetcher.request("GET", url).content


def linked_lists(fetcher, today: date) -> tuple[list[str], list[str]]:
    """The list files each year's polling place page links: (links, pages read)."""
    pages = [(today.year, "upcoming", UPCOMING_PAGE.format(year=today.year))]
    pages += [(y, "history", HISTORY_PAGE.format(year=y)) for y in range(2016, today.year + 1, 2)]
    links, read = [], []
    for year, where, page in pages:
        try:
            html = _get(fetcher, page, f"pages/{year}-{where}.html", refresh=True).decode(
                "utf-8", "replace")  # fmt: skip
        except Exception as e:
            logger.info("%s: %s", page, e)
            continue
        title = re.search(r"<title>([^<]*)", html, re.I)
        if not title or "Elections:" not in title[1]:
            continue  # a missing page answers with the site's 404 page
        read.append(page)
        for href in re.findall(r'href="([^"]+\.pdf)"', html, re.I):
            url = urljoin(page, href.replace("&amp;", "&"))
            if LIST_LINK.search(unquote(url).rsplit("/", 1)[-1]) and not NOT_A_LIST.search(url):
                links.append(url.replace(" ", "%20"))
    return list(dict.fromkeys(links)), read


def county_geoid(name: str, on: str) -> str | None:
    try:
        return sdplaces.county(name, as_of=on).geoidfq
    except (sdplaces.UnknownPlace, sdplaces.AmbiguousPlace):
        return None


def city_geoid(city: str | None) -> str | None:
    """The city's place; a name the list broke over two lines ("WATERTO WN") is tried
    joined up too."""
    return city_place(city) or (city_place(city.replace(" ", "")) if city else None)


def polling_items(listing: parse.Listing, plist: PollingList) -> list[Item]:
    out, taken = [], Counter()
    for row in listing.rows:
        county = row["county"]
        data = {
            "election": plist.election, "election_date": plist.on, "eid": plist.eid,
            "list": POLLING, "list_title": listing.title, "list_updated": listing.updated,
            "county": county, "precinct": row.get("precinct") or None,
            "polling_place": row.get("polling_place") or None,
            "address": row.get("address") or None, "city": row.get("city") or None,
            "state": row.get("state") or None,
            "instructions": row.get("instructions") or None,
            "vote_center": (bool(row["vote_center"]) if "vote_center" in row else None),
            "page": row["page"],
        }  # fmt: skip
        if row.get("carried_to_page"):
            data["carried_to_page"] = row["carried_to_page"]
        base = f"{plist.on} {plist.election} | {county} | {data['precinct'] or ''} | " + (
            data["polling_place"] or "")  # fmt: skip
        taken[base] += 1
        key = base if taken[base] == 1 else f"{base} ({taken[base]})"
        places = [STATE]
        for place in (county_geoid(county, plist.on), city_geoid(data["city"])):
            if place and place not in places:
                places.append(place)
        out.append(Item(key=key, data=data, source_url=f"{plist.url}#page={row['page']}",
                        places=places))  # fmt: skip
    return out


SATELLITE_FIELDS = {"county": re.compile(r"^county$", re.I),
                    "site": re.compile(r"building|location", re.I),
                    "address": re.compile(r"^address$", re.I),
                    "city": re.compile(r"^city", re.I)}  # fmt: skip


def satellite_items(listing: parse.Listing, plist: PollingList) -> list[Item]:
    out, taken = [], Counter()
    for row in listing.rows:
        printed = row["printed"]
        data = {"election": plist.election, "list": SATELLITE, "list_title": listing.title}
        for name, pattern in SATELLITE_FIELDS.items():
            data[name] = next((v for h, v in printed.items() if pattern.search(h)), None) or None
        # "Pennington - Hill City": a county with more than one site names the town
        county = (data["county"] or "").split(" - ")[0]
        data |= {"county_name": county, "printed": printed, "page": row["page"]}
        base = f"{plist.on[:4]} {SATELLITE} | {data['county']} | {data['site']}"
        taken[base] += 1
        key = base if taken[base] == 1 else f"{base} ({taken[base]})"
        places = [STATE]
        for place in (county_geoid(county, plist.on), city_geoid(data["city"])):
            if place and place not in places:
                places.append(place)
        out.append(Item(key=key, data=data, source_url=f"{plist.url}#page={row['page']}",
                        places=places))  # fmt: skip
    return out


def read_list(fetcher, plist: PollingList, today: date) -> bytes:
    """A list's PDF: the stored copy, or fetched from where it is now (or was)."""
    name = f"lists/{plist.slug}.pdf"
    fresh = date.fromisoformat(plist.on) + REFETCH_AFTER >= today
    errors = []
    for url in (plist.url, *plist.earlier):
        try:
            content = _get(fetcher, url, name, refresh=fresh)
        except Exception as e:
            errors.append(f"{url}: {e}")
            continue
        if not content.startswith(b"%PDF"):
            errors.append(f"{url}: not a PDF")
            continue
        return content
    raise RuntimeError("; ".join(errors))


def items(fetcher, today: date | None = None) -> Found:
    today = today or date.today()
    out, read, checks = Found(), set(), {"lists": {}, "unread_lines": {}, "failed": {}}
    for plist in LISTS:
        try:
            content = read_list(fetcher, plist, today)
            listing = (parse.satellite_sites(content) if plist.kind == SATELLITE
                       else parse.read(content))  # fmt: skip
        except Exception as e:  # LayoutChanged or a list that can't be fetched: its records stay
            checks["failed"][plist.slug] = f"{type(e).__name__}: {e}"
            continue
        found = (satellite_items if plist.kind == SATELLITE else polling_items)(listing, plist)
        out += found
        read.add(_prefix(plist))
        checks["lists"][plist.slug] = len(found)
        if listing.unread:
            checks["unread_lines"][plist.slug] = listing.unread[:20]
    links, pages = linked_lists(fetcher, today)
    checks["pages_read"] = len(pages)
    checks["lists_not_in_the_table"] = [u for u in links if known(u) is None]
    checks["lists_no_longer_linked"] = [p.slug for p in LISTS
                                        if not any(same_url(u, p.url) for u in links)]  # fmt: skip
    checks["without_county"] = sum(1 for i in out if len(i.places) < 2)
    no_city = {i.data["city"] or "" for i in out if not any("1600000US" in p for p in i.places)}
    checks["without_city_place"] = sorted(no_city)
    out.checks = {k: v for k, v in checks.items() if v not in ({}, [], 0)}
    out.covers = lambda key: any(key.startswith(p) for p in read)
    return out


def _prefix(plist: PollingList) -> str:
    return (f"{plist.on[:4]} {SATELLITE} |" if plist.kind == SATELLITE
            else f"{plist.on} {plist.election} |")  # fmt: skip


def mentions(record) -> list:
    """People named on a satellite site list: the staff it names ("Carolyn Anderson, City
    Finance Officer, …"), where the line starts with a person's name."""
    printed = record.data.get("printed") or {}
    out = []
    for header, value in printed.items():
        if not re.search(r"staff", header, re.I) or not value:
            continue
        first = value.split(",")[0].strip()
        if not re.fullmatch(r"[A-Z][a-z]+(?: [A-Z]\.?)? [A-Z][a-z'-]+", first):
            continue
        out += from_text(first, dataset=record.recordset.dataset, record_key=str(record),
                         field="staffing", role="satellite site staff", order="given_first",
                         url=record.source_url, start=len(out),
                         context={"site": record.data.get("site"),
                                  "county": record.data.get("county")})  # fmt: skip
    return out


SOURCE = Source(
    slug="polling-places", items=items, mentions=mentions,
    name="Polling places", dataset="polling-places",
    publisher="South Dakota Secretary of State", base_url=f"{CURRENT}/",
    description="Polling places for each statewide election since 2016, by county and "
                "precinct, from the Secretary of State's lists; and the absentee satellite "
                "sites listed for 2018 and 2024.",
)  # fmt: skip
