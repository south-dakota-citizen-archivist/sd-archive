"""Form 990-series returns (990, 990-EZ, 990-PF, 990-T) e-filed by South Dakota's tax-exempt
organizations, from the IRS's Form 990 downloads (2019 on) and, before that, a mirror of the
IRS's e-file releases: one record per return (its
object ID), with the filer, period, totals, and every person the return names (officers,
directors, trustees, key employees and highest-paid employees with their titles, hours and
pay; the signing officer; the preparer).

The organizations are those the IRS's South Dakota extract lists now, and those it listed
before (the irs-exempt-organizations record set, dropped records included). Each year's
index says which returns they filed; each year's zip files are read by HTTP range: the zip's
file list, then only South Dakota's returns. Every return's XML is kept; a return already
kept isn't read again, and a zip's file list is kept once read (published zips don't change).

Returns e-filed before the IRS's downloads begin (2009 to 2018) come from the GivingTuesday
990 data lake, which republishes the IRS's e-file XML as filed (the IRS's own public copy of
those years is no longer online): its newest full index (read by column, over HTTP ranges)
lists the returns, and each one is a file there. A return from the IRS is never replaced by
the mirror's copy; each record says where its XML came from (origin).
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
from collections import defaultdict
from datetime import date

from entities.mentions import from_text
from records.sources import Found, Item, Source
from records.sources._irs.remote_zip import open_zip, read_member
from records.sources._places import city_place

from . import parse

logger = logging.getLogger(__name__)
STATE = "0400000US46"
EO_SD = "https://www.irs.gov/pub/irs-soi/eo_sd.csv"
FIRST_YEAR = 2019
MIRROR = "https://gt990datalake-rawdata.s3.amazonaws.com/"
MIRROR_COLUMNS = ["EIN", "ObjectId", "FormType", "TaxYear", "TaxPeriod", "SubmittedOn"]


def eins(fetcher) -> set[str]:
    """South Dakota's exempt organizations: the IRS extract now, and every one stored."""
    from records.models import Record

    content = fetcher.request("GET", EO_SD, timeout=300).content.decode("utf-8-sig")
    now = {row["EIN"] for row in csv.DictReader(io.StringIO(content))}
    stored = set(Record.objects.filter(recordset__slug="irs-exempt-organizations")
                 .values_list("key", flat=True))  # fmt: skip
    return now | stored


def _cached(fetcher, url: str, name: str) -> bytes:
    store = getattr(fetcher, "store", None)
    if store is not None:
        return fetcher.fetch(url, name)
    return fetcher.request("GET", url, timeout=600).content


def listing(fetcher, url: str) -> list[str]:
    """A zip's member names (kept once read)."""
    store = getattr(fetcher, "store", None)
    name = "listings/" + url.rsplit("/", 2)[-2] + "-" + url.rsplit("/", 1)[1] + ".json"
    if store is not None and store.has(name):
        return json.loads(store.read(name))
    names = open_zip(fetcher, url).namelist()
    if store is not None:
        store.save(name, json.dumps(names).encode(), url)
    return names


def object_id(member: str) -> str:
    return member.rsplit("/", 1)[-1].removesuffix("_public.xml")


def mirror_rows(fetcher, ours: set[str]) -> tuple[str, list[dict]]:
    """South Dakota's rows of the mirror's newest full index (kept once read)."""
    import pyarrow.parquet as pq

    from records.sources._irs.remote_zip import RangeFile

    listing_xml = fetcher.request(
        "GET", MIRROR + "?list-type=2&prefix=Indices/990xmls/index_all_years", timeout=120
    ).text
    keys = sorted(k for k in re.findall(r"<Key>([^<]+\.parquet)</Key>", listing_xml))
    if not keys:
        return "", []
    key = keys[-1]  # the names end in their date
    store = getattr(fetcher, "store", None)
    name = f"mirror/{key.rsplit('/', 1)[1]}.south-dakota.json"
    if store is not None and store.has(name):
        return key, json.loads(store.read(name))
    parquet = pq.ParquetFile(io.BufferedReader(RangeFile(fetcher, MIRROR + key),
                                               buffer_size=1 << 20))  # fmt: skip
    rows = []
    for g in range(parquet.num_row_groups):
        table = parquet.read_row_group(g, columns=MIRROR_COLUMNS).to_pylist()
        rows += [r for r in table if r["EIN"] in ours]
    if store is not None:
        store.save(name, json.dumps(rows).encode(), MIRROR + key)
    return key, rows


def _keep(store, o: str, xml: bytes, where: str, origin: str) -> None:
    store.save(f"xml/{o}.xml", xml, where)
    store.save(f"where/{o}.txt", where.encode(), where)
    store.save(f"origin/{o}.txt", origin.encode(), where)


def items(fetcher, today: date | None = None, mirror: bool = True) -> Found:
    today = today or date.today()
    store = getattr(fetcher, "store", None)
    page = fetcher.request("GET", parse.DOWNLOADS, timeout=120).text
    zips, indexes = parse.zip_links(page), parse.index_links(page)
    ours = eins(fetcher)
    rows: dict[str, dict] = {}  # object ID → its index row
    counts: dict = defaultdict(int)
    for year in sorted(y for y in indexes if y >= FIRST_YEAR):
        # a past year's index is fixed; this year's and last year's grow, so they're re-read
        recent = year >= today.year - 1
        name = f"index/{year}" + (f"-{today:%Y-%m}" if recent else "") + ".csv"
        content = _cached(fetcher, indexes[year], name).decode("utf-8-sig", "replace")
        wanted = {r["OBJECT_ID"]: r for r in csv.DictReader(io.StringIO(content))
                  if r["EIN"] in ours}  # fmt: skip
        counts[f"{year} returns"] = len(wanted)
        rows.update(wanted)
        todo = {o for o in wanted if not (store is not None and store.has(f"xml/{o}.xml"))}
        for url in zips.get(year, []):
            if not todo:
                break
            members = {object_id(m): m for m in listing(fetcher, url)}
            here = todo & set(members)
            if not here:
                continue
            archive = open_zip(fetcher, url)
            for o in sorted(here):
                if store is not None:
                    xml = read_member(archive, members[o])
                    _keep(store, o, xml, f"{url}#{members[o]}", "irs")
            todo -= here
        counts[f"{year} not in any zip"] = len(todo)
    if mirror:
        key, found = mirror_rows(fetcher, ours)
        counts["mirror index"] = key
        new = [r for r in found if r["ObjectId"] not in rows]
        counts["mirror returns not from the IRS"] = len(new)
        for r in new:
            o = r["ObjectId"]
            rows[o] = r
            if store is None or store.has(f"xml/{o}.xml"):
                continue
            url = f"{MIRROR}EfileData/XmlFiles/{o}_public.xml"
            xml = fetcher.request("GET", url, timeout=120).content
            if parse.parse(xml)["ein"] != r["EIN"]:  # the file is the return the index says
                counts["mirror files not matching the index"] += 1
                continue
            _keep(store, o, xml, url, "GivingTuesday 990 data lake (IRS e-file XML)")
    out = Found()
    for o, row in rows.items():
        if store is None or not store.has(f"xml/{o}.xml"):
            continue
        ret = parse.parse(store.read(f"xml/{o}.xml"))
        where = store.read(f"where/{o}.txt").decode() if store.has(f"where/{o}.txt") else ""
        origin = store.read(f"origin/{o}.txt").decode() if store.has(f"origin/{o}.txt") else "irs"
        data = {"object_id": o, "origin": origin, "index": row, **ret}
        city = (city_place(ret["address"].get("city"))
                if ret["address"].get("state") == "SD" else None)  # fmt: skip
        out.append(Item(key=o, data=data, source_url=where or parse.DOWNLOADS,
                        places=[STATE, *([city] if city else [])]))  # fmt: skip
    out.checks = {"organizations": len(ours), **counts, "returns": len(out)}
    return out


def mentions(record) -> list:
    d = record.data
    common = {"dataset": record.recordset.dataset, "record_key": str(record),
              "url": record.source_url,
              "context": {"ein": d["ein"], "organization": d["name"],
                          "tax_year": d["tax_year"]}}  # fmt: skip
    out = from_text(d["name"], field="name", role="filer", **common)
    for p in d["people"]:
        out += from_text(p["name"], field="people", role=p["title"] or p["role"],
                         order="given_first" if p["kind"] == "person" else "auto",
                         start=len(out), **common)  # fmt: skip
    if (s := d.get("signed_by")) and s.get("name"):
        out += from_text(s["name"], field="signed_by", role=s.get("title") or "signer",
                         order="given_first", start=len(out), **common)  # fmt: skip
    if (p := d.get("preparer")) and p.get("person"):
        out += from_text(p["person"], field="preparer", role="paid preparer",
                         order="given_first", start=len(out), **common)  # fmt: skip
    return out


SOURCE = Source(
    slug="irs-990-returns", items=items, mentions=mentions, complete=False,
    name="Form 990 returns (IRS)", dataset="irs-990-returns",
    publisher="Internal Revenue Service", base_url=parse.DOWNLOADS,
    description="Form 990, 990-EZ, 990-PF and 990-T returns e-filed by South Dakota's "
                "tax-exempt organizations: totals, officers, directors, key employees and "
                "their pay.",
)  # fmt: skip
