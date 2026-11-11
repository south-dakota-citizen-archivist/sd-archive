"""Burials in South Dakota's cemeteries that publish on BurialSearch (Bear Butte in Sturgis,
Britton, Belle Fourche's cemeteries, Rose Hill in Spearfish, and any added later): one
record per burial, with its place on the cemetery's map and the burial record the cemetery
publishes (names, birth, death and burial dates, veteran service, location, owners; the
fields vary by cemetery and are kept as published, with the dates also as ISO dates).

Each cemetery's map gives every burial and its space; each space's burial records come from
the page's space-info component, one request per space. Those are kept by month (a stopped
run resumes; a burial keeps the last record read), at most `DETAILS_PER_RUN` a run; until
every burial has its record, the run is partial (nothing is marked dropped). The person, the
maiden name and the owners are mentioned for matching.
"""

from __future__ import annotations

import json
import random
from datetime import date, datetime

from entities.mentions import from_text
from records.sources import Found, Item, Source, Stopped
from records.sources import _burialsearch as B
from records.sources._places import city_place

STATE = "0400000US46"
DETAILS_PER_RUN = 15000
SKIP = {"media", "schema", "s", "cemetery"}


def cemeteries(fetcher) -> list[dict]:
    """South Dakota's cemeteries in the BurialSearch directory."""
    page = fetcher.request("GET", B.BASE_URL + "/", timeout=120).text
    return [c for c in B.directory(page) if c["address"].endswith(", SD")]


def _iso(value) -> str | None:
    try:
        return datetime.strptime(str(value).strip(), "%m/%d/%Y").date().isoformat()
    except ValueError:
        return None


def _geometry(feature: dict) -> bytes | None:
    import shapely
    from shapely.geometry import shape

    geom = feature.get("geometry")
    return shapely.to_wkb(shape(geom), output_dimension=2) if geom else None


def details(fetcher, session, store, month: str, schema: str, sid: str, budget: list[int]):
    """A space's burial records: this month's stored copy, else read (while the budget
    lasts), else the latest stored; None if never read."""
    name = f"details/{schema}/{month}/{sid}.json"
    if store is not None and store.has(name):
        return json.loads(store.read(name))
    if budget[0] > 0:
        budget[0] -= 1
        found = session.burials_in(sid, schema)
        if store is not None:
            store.save(name, json.dumps(found).encode(), session.url, method="POST")
        return found
    if store is not None:
        earlier = sorted(store.root.glob(f"details/{schema}/*/{sid}.json"), reverse=True)
        if earlier:
            return json.loads(earlier[0].read_bytes())
    return None


def items(fetcher, today: date | None = None, details_per_run: int | None = None) -> Found:
    month = f"{today or date.today():%Y-%m}"
    store = getattr(fetcher, "store", None)
    budget = [DETAILS_PER_RUN if details_per_run is None else details_per_run]
    out, counts, missing = Found(), {}, 0
    for cem in cemeteries(fetcher):
        session = B.Session(fetcher, cem["url"])
        layers = session.layers()
        if store is not None:
            store.save(f"layers/{cem['schema']}/{month}.json", json.dumps(layers).encode(),
                       cem["url"], method="POST")  # fmt: skip
        burials = B.features(layers, "burials")
        city = city_place(cem["address"].rsplit(",", 1)[0])
        records: dict[str, dict] = {}
        sids = sorted({str(f["properties"]["sid"]) for f in burials if f["properties"].get("sid")})
        random.shuffle(sids)
        for sid in sids:
            for rec in details(fetcher, session, store, month, cem["schema"], sid, budget) or []:
                records[str(rec["burial_id"])] = rec
        for feature in burials:
            props = feature["properties"]
            bid = str(props.get("bid"))
            rec = records.get(bid)
            data = {"cemetery": cem["name"], "cemetery_address": cem["address"],
                    "cemetery_schema": cem["schema"], "map": props}  # fmt: skip
            if rec:
                data["record"] = {k: v for k, v in rec.items() if k not in SKIP}
                data["photos"] = [m["path"] for m in rec.get("media") or [] if m.get("path")]
                for field in ("Birth Date", "Death Date", "Burial Date"):
                    data[field.lower().replace(" ", "_")] = _iso(rec.get(field) or "")
            else:
                missing += 1
            out.append(Item(key=f"{cem['schema']}:{bid}", data=data, source_url=cem["url"],
                            geometry=_geometry(feature),
                            places=[STATE, *([city] if city else [])]))  # fmt: skip
        with_record = sum(1 for f in burials if str(f["properties"].get("bid")) in records)
        counts[cem["name"]] = {"burials": len(burials), "with_record": with_record,
                               "spaces": len(B.features(layers, "spaces"))}  # fmt: skip
    out.checks = {"cemeteries": counts, "without_record": missing}
    if missing:
        raise Stopped(out, f"{missing} burials without their record read yet")
    return out


def mentions(record) -> list:
    d, out = record.data, []
    rec = d.get("record") or {}
    common = {"dataset": record.recordset.dataset, "record_key": str(record),
              "url": record.source_url,
              "context": {"cemetery": d["cemetery"], "death_date": d.get("death_date"),
                          "location": rec.get("Location")}}  # fmt: skip
    name = " ".join(x for x in (rec.get("fname"), rec.get("middle"), rec.get("lname")) if x)
    name = name or (rec.get("Interred") or "").strip()
    if name:
        out += from_text(name, field="interred", role="interred", order="given_first",
                         **common)  # fmt: skip
    if rec.get("Maiden Name") and rec.get("fname"):
        out += from_text(f"{rec['fname']} {rec['Maiden Name']}", field="maiden_name",
                         role="interred (maiden name)", order="given_first", start=len(out),
                         **common)  # fmt: skip
    for field in ("Owner", "Co-Owners"):
        value = (rec.get(field) or "").strip()
        # "Family McPhee" names a family, not a person
        if value and value.upper() not in ("NONE", "N/A") and not value.startswith("Family "):
            out += from_text(value, field=field, role="lot owner", start=len(out), **common)
    return out


SOURCE = Source(
    slug="cemetery-burials", items=items, mentions=mentions, name="Cemetery burials",
    dataset="cemetery-burials", publisher="Cemeteries publishing on BurialSearch",
    base_url=B.BASE_URL,
    description="Burials in South Dakota cemeteries that publish their records on "
                "BurialSearch: names, dates, veteran service, plot location and map.",
)  # fmt: skip
