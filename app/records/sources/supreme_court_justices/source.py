"""Every justice of the South Dakota Supreme Court since statehood, from the Unified Judicial
System's "Supreme Court Photographic History": one record per justice, with district, years
on the court, whether they served as presiding judge or chief justice (and the years as chief
justice), and whether they joined when the court grew from three to five in 1909. Keyed by
name."""

from __future__ import annotations

from django.utils.text import slugify

from entities.mentions import from_text
from records.sources import Found, Item, Source

from . import parse

STATE = "0400000US46"


def items(fetcher) -> Found:
    pdf = fetcher.request("GET", parse.URL, timeout=300).content
    members, chiefs, pages = parse.lists(pdf)
    chief_terms: dict[str, list] = {}
    for c in chiefs:
        chief_terms.setdefault(c["name"], []).append({"start": c["start"], "end": c["end"]})
    out = Found()
    for m in members:
        data = {"name": m["name"], "district": m["district"], "start": m["start"],
                "end": m["end"], "sitting": m["end"] is None,
                "presiding_judge": "*" in m["marks"] and "**" not in m["marks"],
                "chief_justice": "**" in m["marks"],
                "chief_justice_terms": chief_terms.get(m["name"], []),
                "joined_in_1909_expansion": "+" in m["marks"],
                "as_published": m["as_published"]}  # fmt: skip
        url = f"{parse.URL}#page={pages['members']}"
        out.append(Item(key=slugify(m["name"]), data=data, source_url=url, places=[STATE]))
    missing = sorted(set(chief_terms) - {m["name"] for m in members})
    out.checks = {"members": len(members), "chief_justices": len(chief_terms), "pages": pages,
                  "chief_justices_not_members": missing}  # fmt: skip
    return out


def mentions(record) -> list:
    d = record.data
    return from_text(d["name"], dataset=record.recordset.dataset, record_key=str(record),
                     field="name", role="supreme court justice", order="given_first",
                     url=record.source_url,
                     context={"district": d["district"], "start": d["start"],
                              "end": d["end"]})  # fmt: skip


SOURCE = Source(
    slug="supreme-court-justices", items=items, mentions=mentions,
    name="South Dakota Supreme Court justices", dataset="supreme-court-justices",
    publisher="South Dakota Unified Judicial System", base_url=parse.URL,
    description="Every justice of the South Dakota Supreme Court since 1889: district, years "
                "on the court, presiding judges and chief justices.",
)  # fmt: skip
