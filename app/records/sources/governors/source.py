"""South Dakota's governors, from the National Governors Association: one record per governor
(the former governors the NGA lists, and the current one), with terms, party, birth and
death, school, family, military service, a biography and its sources. The portrait is the
record's document. Keyed by the governor's page on nga.org."""

from __future__ import annotations

from entities.mentions import from_text
from records.sources import DocumentLink, Found, Item, Source

from . import parse

STATE = "0400000US46"


def key(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[1]


def items(fetcher) -> Found:
    listed = parse.governors(fetcher.request("GET", parse.LIST, timeout=60).content)
    pages = {g["url"]: g for g in listed}
    out, current = Found(), None
    for url in list(pages):
        html = fetcher.request("GET", url, timeout=60).content
        current = current or parse.current_governor(html)
        pages[url] = {**pages[url], **parse.governor(html), "current": False}
    if current and current not in pages:
        html = fetcher.request("GET", current, timeout=60).content
        pages[current] = {"url": current, **parse.governor(html), "current": True}
    for url, data in pages.items():
        docs = [DocumentLink(data["portrait"], metadata={"label": "Portrait"})] if (
            data.get("portrait")) else []  # fmt: skip
        out.append(Item(key=key(url), data=data, source_url=url, documents=docs,
                        places=[STATE]))  # fmt: skip
    out.checks = {"listed": len(listed), "current": current}
    return out


def mentions(record) -> list:
    d = record.data
    return from_text(d["name"], dataset=record.recordset.dataset, record_key=str(record),
                     field="name", role="governor", order="given_first", url=record.source_url,
                     context={"terms": [t["as_published"] for t in d["terms"]],
                              "born": d.get("born"), "died": d.get("died")})  # fmt: skip


SOURCE = Source(
    slug="governors", items=items, mentions=mentions, ingest="documents",
    name="South Dakota governors", dataset="governors",
    publisher="National Governors Association", base_url=parse.LIST,
    description="Every governor of South Dakota, with terms, party, biography and portrait.",
)  # fmt: skip
