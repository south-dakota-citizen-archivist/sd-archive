"""Documents posted in the Documents section of every school district in the registry whose
website is on Thrillshare (records.sources._governments): board agendas and minutes,
policies, budgets, handbooks and the rest, one record per file, with its folder path and
the date its name gives. The file is the record's document (a link to a page on another
site, such as a sign-up form, is kept but not downloaded)."""

from __future__ import annotations

from records.sources import DocumentLink, Found, Item, Source
from records.sources import _thrillshare as T
from records.sources._governments import using

STATE = "0400000US46"


def items(fetcher) -> Found:
    out, counts = Found(), {}
    for gov in using("thrillshare"):
        site = gov["setting"]
        found = T.files(fetcher, site)
        seen = set()
        for f in found:
            key = f"{site}:{f['key'] or f['url']}"
            if key in seen:
                continue
            seen.add(key)
            data = {**f, "date": T.date_named(f["name"]), "government": gov["name"],
                    "folder": " / ".join(f["folder_path"])}  # fmt: skip
            # a link to another site (a sign-up form) is kept, not downloaded
            docs = ([DocumentLink(f["url"], metadata={"label": f["name"]})]
                    if T.is_file(f["url"]) else [])  # fmt: skip
            out.append(Item(key=key, data={**data, "linked_page": not T.is_file(f["url"])},
                            source_url=f["url"], documents=docs,
                            places=[STATE, gov["place"]]))  # fmt: skip
        counts[gov["name"]] = {"files": len(seen)}
    out.checks = {"governments": counts}
    return out


SOURCE = Source(
    slug="thrillshare-documents", items=items, ingest="documents",
    name="School district documents (Thrillshare)", dataset="school-district-documents",
    publisher="School districts (Thrillshare sites)",
    description="Board agendas and minutes, policies, budgets and other documents school "
                "districts post on their Thrillshare websites.",
)  # fmt: skip
