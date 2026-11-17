"""Health professional licenses: every license on the lists published by the licensing boards
the Department of Health hosts (doh.sd.gov/licensing-and-records/boards/). One record a
license, keyed by its list and license number (and its type, where a list's numbers repeat
across types: physical therapists and assistants).

The lists (boards.LISTS) are rosters the boards' licensing portal generates, PDFs the boards
post, the midwives' PDF and two licensee apps' CSV downloads; each is kept as fetched
(raw/health-licenses/lists/) and read by its kind (parse.py). The lists are of current
licenses; a license gone from its list is kept, marked dropped.

Each table list is checked against its own text: every license number in the text (by the
list's pattern) must be read, and no line may be left out of a row. The Board of Pharmacy's
lookups, and the Boards of Dentistry, Medical and Osteopathic Examiners, and Nursing (each
with a site of its own), aren't read yet.

The lists change as licenses are issued and renewed; a weekly run keeps up.
"""

from __future__ import annotations

import io
import re
from collections import Counter
from pathlib import Path

import sdplaces
from entities.mentions import from_text
from records.sources import Found, Item, Source
from records.sources._places import city_place

from . import boards, parse
from .schema import License

STATE = "0400000US46"


def _browser_csv(url: str) -> bytes:
    """A licensee app's whole list: its Download button's CSV (the app runs in the browser)."""
    import sdfetch

    with sdfetch.browser("stealth") as b:
        b.get(url)
        b.page.wait_for_selector("text=Download", timeout=60000)
        with b.page.expect_download(timeout=60000) as d:
            b.page.get_by_text("Download", exact=True).first.click()
        return Path(d.value.path()).read_bytes()


def _places(lic: dict) -> list[str]:
    """The state, and the South Dakota city or town the list gives: the licensee's, or for a
    nursing facility administrator, their facility's."""
    out = [STATE]
    city = lic["city"] or lic["details"].get("facility_city")
    state = lic["state"] or lic["details"].get("facility_state") or "SD"
    if city and state == "SD":
        try:
            geoid = city_place(city)
        except sdplaces.AmbiguousPlace:
            geoid = None
        if geoid:
            out.append(geoid)
    return out


def key(lst: boards.List, lic: dict) -> str:
    if lst.by_type:
        return f"{lst.slug}|{lic['license_type']}|{lic['license_number']}"
    return f"{lst.slug}|{lic['license_number']}"


def read_list(lst: boards.List, content: bytes) -> tuple[list[dict], dict]:
    """A list's licenses, and its checks (strays; license numbers in its text not read)."""
    import pdfplumber

    checks: dict = {}
    if lst.kind == "csv":
        return parse.csv_rows(lst, content), checks
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        text = "\n".join(p.extract_text() or "" for p in pdf.pages)
        if lst.kind == "midwives":
            rows = parse.midwives(text)
            heads = [x for x in text.splitlines() if parse.HEAD.match(x.strip())]
            if len(heads) != len(rows):
                checks["licenses in the text not read"] = len(heads) - len(rows)
            return [parse.license(lst, r) for r in rows], checks
        rows, strays = parse.table(lst, pdf)
    if strays:
        checks["lines in no row"] = strays
    if lst.ids:
        missing = set(re.findall(lst.ids, text)) - {r["license_number"] for r in rows}
        if missing:
            checks["license numbers in the text not read"] = sorted(missing)
    as_of = re.search(r"\bas of\s+([\d/-]+)", text)
    if as_of:
        checks["as of"] = as_of[1]
    return rows, checks


def items(fetcher) -> Found:
    found, checks, counts = {}, {}, Counter()
    for lst in boards.LISTS:
        name = f"lists/{lst.slug}.{'csv' if lst.kind == 'csv' else 'pdf'}"
        if lst.kind == "csv":
            url = lst.page
            content = _browser_csv(url)
            fetcher.store.save(name, content, url, note={"via": "browser download"})
        else:
            page = fetcher.request("GET", lst.page).text
            url = boards.find(page, lst.page, lst.link)
            content = fetcher.fetch(url, name, refresh=True)
        licenses, list_checks = read_list(lst, content)
        if not licenses:
            raise ValueError(f"{lst.slug}: no licenses read from {url}")
        for lic in licenses:
            k = key(lst, lic)
            if k in found:
                list_checks.setdefault("license numbers listed twice", []).append(k)
                continue
            found[k] = Item(key=k, data=License(**lic).model_dump(mode="json"),
                            source_url=url, places=_places(lic))  # fmt: skip
            counts[lst.slug] += 1
        if list_checks:
            checks[lst.slug] = list_checks
    out = Found(found.values())
    out.checks = {"licenses by list": dict(counts), **checks}
    return out


def _organization(name: str, **common):
    import sdnames
    from entities.models import Mention

    schema = sdnames.ftm_schema(name)
    return Mention(schema=schema if schema in ("Company", "PublicBody") else "Organization",
                   name=name, normalized=sdnames.normalize(name), properties={},
                   field="name", position=0, original=name, **common)  # fmt: skip


def mentions(record) -> list:
    d = record.data
    common = {"dataset": record.recordset.dataset, "record_key": str(record),
              "url": record.source_url,
              "context": {"board": d["board"], "profession": d["profession"],
                          "license_number": d["license_number"],
                          "license_type": d["license_type"]}}  # fmt: skip
    if d["holder"] == "establishment":
        return [_organization(d["name"], role="licensed establishment", **common)]
    return from_text(d["name"], field="name", order="given_first", role="licensee",
                     **common)  # fmt: skip


SOURCE = Source(
    slug="health-licenses", items=items, mentions=mentions, max_drop=0.2,
    name="Health professional licenses", dataset="health-licenses",
    publisher="South Dakota Department of Health (licensing boards)",
    base_url="https://doh.sd.gov/licensing-and-records/boards/",
    description="Every license on the published lists of the licensing boards the Department "
                "of Health hosts: chiropractors, optometrists, speech-language pathologists, "
                "funeral directors and establishments, crematories, audiologists and hearing "
                "aid dispensers, massage therapists, midwives, nursing facility "
                "administrators, physical therapists and podiatrists. Each gives the license "
                "number and type, the holder, dates and whether there has been discipline.",
)  # fmt: skip
