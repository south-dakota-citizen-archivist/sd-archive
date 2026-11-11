"""Meetings posted on CivicClerk by every government in the registry that uses it
(records.sources._governments): one record per event, with its board, time, place, video
link, and published files (agendas, packets, minutes), which are the record's documents.
Videos are linked, not downloaded."""

from __future__ import annotations

from records.sources import DocumentLink, Found, Item, Source
from records.sources import _civicclerk as C
from records.sources._governments import using

STATE = "0400000US46"


def items(fetcher) -> Found:
    out, counts = Found(), {}
    for gov in using("civicclerk"):
        tenant = gov["setting"]
        found = C.events(fetcher, tenant)
        for event in found:
            data = {**C.meeting(event, tenant), "government": gov["name"]}
            docs = [DocumentLink(C.file_url(tenant, f["file_id"]),
                                 metadata={"label": f["type"], "name": f["name"],
                                           "portal_url": f["portal_url"]})
                    for f in data["files"]]  # fmt: skip
            out.append(Item(key=f"{tenant}:{event['id']}", data=data,
                            source_url=data["portal_url"], documents=docs,
                            places=[STATE, gov["place"]]))  # fmt: skip
        with_files = sum(1 for e in found if e.get("publishedFiles"))
        counts[gov["name"]] = {"events": len(found), "with_files": with_files}
    out.checks = {"governments": counts}
    return out


SOURCE = Source(
    slug="civicclerk-meetings", items=items, ingest="documents",
    name="Meetings (CivicClerk)", dataset="local-government-meetings",
    publisher="Local governments (CivicClerk portals)",
    description="Every meeting the governments using CivicClerk have posted, with agendas, "
                "packets, minutes and video links.",
)  # fmt: skip
