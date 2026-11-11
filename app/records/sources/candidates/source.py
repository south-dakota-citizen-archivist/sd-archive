"""Candidates on South Dakota ballots, from the Secretary of State's candidate lists
(vip.sdsos.gov): every contest from federal offices to precinct committee seats, school
boards, city wards and water districts, for each election since 2012 that has a list.

Elections come from the elections pipeline's catalog (pipelines/elections/data/
elections.csv): the same election IDs (eid) as the results site. One record per candidacy,
keyed by election, contest, district and name (a second, different listing of the same
candidacy gets a numbered key). Each is tied to the place the contest is in
(a legislative district, county, school district or city, when sdplaces finds exactly one)
and to the candidate's mailing city. A run speaks for the elections it read.
"""

from __future__ import annotations

import csv
import logging
from datetime import date
from pathlib import Path

import sdplaces
from entities.mentions import from_text
from records.sources import Found, Item, Source, _sdcfr
from records.sources._places import city_place
from records.sources._tables import text
from sddocs.clean import parse_date

logger = logging.getLogger(__name__)

URL = "https://vip.sdsos.gov/candidatelist.aspx?eid={}"
GRID = "ctl00_MainContent_grdCandidates"
COLUMNS = ["Contest", "Name", "Party", "Petition Filing Date", "District/County", "Ballot Order",
           "WithdrawnDate", "Mailing Address", "OfficeSeqNum", "elFirstName", "elMiddleName",
           "elLastName", "elSuffix", "Status", "elType", "eldate", "elAddress1", "elAddress2",
           "elCity", "elState", "elZip", "DistrictType", "Elector1"]  # fmt: skip
FIELDS = ["contest", "name", "party", "filed", "district", "ballot_order", "withdrawn",
          "mailing_address", "office_seq", "first_name", "middle_name", "last_name", "suffix",
          "status", "election_type", "election_date", "address1", "address2", "city", "state",
          "zip", "district_type", "elector"]  # fmt: skip
STATE = "0400000US46"


def elections() -> list[dict]:
    from django.conf import settings

    path = Path(settings.BASE_DIR).parent / "pipelines" / "elections" / "data" / "elections.csv"
    with path.open() as f:
        return list(csv.DictReader(f))


def _iso(value: str) -> str | None:
    on = parse_date(value) if value else None
    return on.isoformat() if on else None


def contest_place(district_type: str, district: str) -> str | None:
    """The fully qualified GEOID of the place a contest is in, when there's exactly one."""
    try:
        if district_type == "SW":
            return STATE
        if district_type == "SEN" and district:
            return sdplaces.senate_district(district.split()[-1].lstrip("0")).geoidfq
        if district_type == "HOU" and district:
            return sdplaces.house_district(district.split()[-1].lstrip("0")).geoidfq
        if district_type in ("CTY", "COM", "PRE") and district:
            # "Davison", "Aurora-1", "Lyman - District 3-4-5", "Aurora - Precinct-2"
            name = district.split(" - ")[0] if " - " in district else district.rsplit("-", 1)[0]
            return sdplaces.county(name if district_type != "CTY" else district).geoidfq
        if district_type in ("SCH", "SBR") and district:
            return sdplaces.school_district(district).geoidfq
        if district_type in ("MUN", "WAR") and district:
            return city_place(district.split(" Ward")[0])
    except (sdplaces.UnknownPlace, ValueError):
        return None
    return None


def rows(fetcher, eid: str, month: str) -> list[dict]:
    out = []
    for cells in _sdcfr.grid(fetcher, URL.format(eid), GRID, ["Command item", *COLUMNS[:3]],
                             store_as=f"lists/{month}/{eid}"):  # fmt: skip
        values = [text(c) for c in cells[: len(COLUMNS)]]
        row = dict(zip(FIELDS, values, strict=False))
        row["filed"], row["withdrawn"] = _iso(row["filed"]), _iso(row["withdrawn"])
        row["election_date"] = _iso((row["election_date"] or "").split()[0])
        out.append(row)
    return out


def key(eid: str, row: dict) -> str:
    return f"{eid} {row['contest']} | {row['district']} | {row['name']}"


def items(fetcher) -> Found:
    month = date.today().strftime("%Y-%m")
    out, read, empty, taken = Found(), set(), [], set()
    for e in elections():
        found = rows(fetcher, e["eid"], month)
        if not found:
            empty.append(e["eid"])
            continue
        read.add(e["eid"])
        for row in found:
            row = {**row, "eid": int(e["eid"]), "election": e["title"]}
            places = [STATE]
            for place in (contest_place(row["district_type"], row["district"]),
                          city_place(row["city"]) if row["state"] == "SD" else None):  # fmt: skip
                if place and place not in places:
                    places.append(place)
            k, n = key(e["eid"], row), 1
            while k in taken:  # listed twice, differently (Otten, 2016: two filing dates)
                n += 1
                k = f"{key(e['eid'], row)} ({n})"
            taken.add(k)
            out.append(Item(key=k, data=row, source_url=URL.format(e["eid"]), places=places))
    out.checks = {"elections_with_lists": len(read), "elections_without": empty}
    out.covers = lambda k: k.split(" ", 1)[0] in read
    return out


def mentions(record) -> list:
    d = record.data
    return from_text(d["name"], dataset=record.recordset.dataset, record_key=str(record),
                     field="name", role="candidate", order="given_first", url=record.source_url,
                     context={"contest": d["contest"], "election": d["election"],
                              "party": d["party"]})  # fmt: skip


SOURCE = Source(
    slug="candidates", items=items, mentions=mentions,
    name="Candidates", dataset="candidates", publisher="South Dakota Secretary of State",
    base_url="https://vip.sdsos.gov/",
    description="Candidates on South Dakota ballots, from federal offices to precinct "
                "committee seats, school boards and city wards, for each election since 2012 "
                "with a candidate list.",
)  # fmt: skip
