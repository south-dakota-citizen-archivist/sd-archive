"""Newspapers in the South Dakota NewsMedia Association's member directory: each paper's
city and county, website, whether it's a legal newspaper (one qualified to print public
notices), its mailing address, phone and email, and its publisher.

The directory page (a Wix site) shows a table widget that loads its rows from the widget's
own API, which answers only with the site's access token for the widget app. So a run reads
the page (for the table's ID, the widget's host and its app), the site's access tokens
(/_api/v1/access-tokens), then the table: every paper in one answer. Plain HTTP; no
browser.

One record per paper, keyed by its name and city (unique in the directory). Each is tied to
the state, its county and the places its city cell names ("North Sioux City/Dakota Dunes"
names two). The table is kept by hand on the site: check it about monthly.
"""

from __future__ import annotations

import contextlib
import re

import sdplaces
from entities.mentions import from_text
from records.sources import Item, Source
from records.sources._places import city_place

from . import parse

PAGE = "https://www.sdna.com/directory"
TOKENS = "https://www.sdna.com/_api/v1/access-tokens"
STATE = "0400000US46"
MIN_PAPERS = 60  # the directory lists about 110


def table(fetcher) -> dict:
    found = parse.widget(fetcher.request("GET", PAGE).text)
    tokens = fetcher.request("GET", TOKENS).json()
    instance = ((tokens.get("apps") or {}).get(found["app"]) or {}).get("instance")
    if not instance:
        raise parse.LayoutChanged("the site's access tokens have none for the table widget")
    url = f"https://{found['host']}/api/table?id={found['table']}"
    headers = {"authorization": instance, "origin": "https://www.sdna.com",
               "referer": "https://www.sdna.com/"}  # fmt: skip
    return fetcher.request("GET", url, headers=headers).json()


def key(paper) -> str:
    return f"{paper.publication_name} ({paper.city})"


def places(paper) -> list[str]:
    out = [STATE]
    with contextlib.suppress(sdplaces.UnknownPlace, sdplaces.AmbiguousPlace):
        out.append(sdplaces.county(paper.county).geoidfq)
    out += [g for c in paper.cities if (g := city_place(c))]
    return list(dict.fromkeys(out))


def items(fetcher) -> list[Item]:
    papers = parse.newspapers(table(fetcher))
    if len(papers) < MIN_PAPERS:
        raise parse.LayoutChanged(f"only {len(papers)} papers in the directory")
    return [Item(key=key(p), data=p.model_dump(mode="json"), source_url=PAGE,
                 places=places(p)) for p in papers]  # fmt: skip


# words that make a publisher an organization here, where sdnames would read a person
PRESS_WORDS = re.compile(
    r"^south dakota\b|\b(publishing|publications?|publishers?|printing|press|media|"
    r"news(?:papers?)?|newsroom|gazette|"
    r"courier|herald|tribune|journal|chronicle|times|record|leader|weekly|booster|argus|"
    r"pioneer|clarion|reporter|review|independent|communications|group|company|university)\b",
    re.I)  # fmt: skip


def _organization(name: str, field: str, role: str, position: int, **common):
    """One mention of an organization, whole ("Tyndall Tribune & Register" is one paper)."""
    import sdnames
    from entities.models import Mention

    schema = sdnames.ftm_schema(name)
    return Mention(schema=schema if schema in ("Company", "PublicBody") else "Organization",
                   name=name, normalized=sdnames.normalize(name), properties={}, field=field,
                   position=position, role=role, original=name, **common)  # fmt: skip


def _is_organization(name: str) -> bool:
    import sdnames

    return (sdnames.ftm_schema(name) != "Person" or len(name.split()) == 1
            or bool(PRESS_WORDS.search(name)))  # fmt: skip


def mentions(record) -> list:
    """The paper (an organization), and each company or person its publisher cell names."""
    d = record.data
    common = {"dataset": record.recordset.dataset, "record_key": str(record),
              "url": record.source_url,
              "context": {"newspaper": d["publication_name"], "city": d["city"]}}  # fmt: skip
    out = [_organization(d["publication_name"], "publication_name", "newspaper", 0, **common)]
    for name in d["publishers"]:
        if _is_organization(name):
            out.append(_organization(name, "publisher", "newspaper publisher", len(out),
                                     **common))  # fmt: skip
        else:
            out += from_text(name, field="publisher", role="newspaper publisher",
                             order="given_first", start=len(out), **common)  # fmt: skip
    return out


SOURCE = Source(
    slug="newspaper-directory", items=items, mentions=mentions, max_drop=0.2,
    name="Newspapers (press association directory)", dataset="newspapers",
    publisher="South Dakota NewsMedia Association", base_url=PAGE,
    description="South Dakota's newspapers as the press association lists them: city and "
                "county, website, whether each is a legal newspaper, contact details and "
                "publisher.",
)  # fmt: skip
