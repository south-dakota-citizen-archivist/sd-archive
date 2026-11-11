"""CivicClerk, the meetings portal many governments use (<tenant>.portal.civicclerk.com), read
through the OData API the portal itself uses (<tenant>.api.civicclerk.com/v1): every event
(meeting) the government has posted, with its board, time, place, video, and published
files (agenda, packet, minutes, …).

Links point at the public portal; files are downloaded through the API's file stream (the
API's own file links are signed and expire)."""

from __future__ import annotations

KEEP = ["id", "eventName", "eventDate", "startDateTime", "categoryName", "eventCategoryName",
        "agendaId", "agendaName", "hasAgenda", "hasMedia", "isOnDemandEvent",
        "externalMediaUrl", "youtubeVideoId"]  # fmt: skip


def api(tenant: str) -> str:
    return f"https://{tenant}.api.civicclerk.com/v1"


def portal(tenant: str) -> str:
    return f"https://{tenant}.portal.civicclerk.com"


def file_url(tenant: str, file_id: int) -> str:
    return f"{api(tenant)}/Meetings/GetMeetingFileStream(fileId={file_id},plainText=false)"


def events(fetcher, tenant: str) -> list[dict]:
    """Every event, oldest first, following the API's next links."""
    out = []
    url = f"{api(tenant)}/Events"
    params = {"$orderby": "startDateTime asc,id asc", "$count": "true"}
    while url:
        data = fetcher.request("GET", url, params=params, timeout=120,
                               headers={"Accept": "application/json"}).json()  # fmt: skip
        out += data.get("value") or []
        url, params = data.get("@odata.nextLink"), None
    return out


def meeting(event: dict, tenant: str) -> dict:
    """An event as a record: its own fields, the place, the video, and the files."""
    loc = event.get("eventLocation") or {}
    place = ", ".join(x for x in (loc.get("address1"), loc.get("address2"), loc.get("city"),
                                  loc.get("state"), loc.get("zipCode")) if x)  # fmt: skip
    video = event.get("mediaSourcePathMp4") or event.get("mediaStreamPath") or None
    files = [{"type": f.get("type"), "name": f.get("name"), "file_id": f.get("fileId"),
              "published": f.get("publishOn"),
              "portal_url": f"{portal(tenant)}/event/{event['id']}/files/"
                            f"{(f.get('type') or 'agenda').lower().replace(' ', '-')}/"
                            f"{f.get('fileId')}"}
             for f in event.get("publishedFiles") or [] if f.get("fileId")]  # fmt: skip
    return {**{k: event.get(k) for k in KEEP}, "location": place or None, "video": video,
            "files": files, "portal_url": f"{portal(tenant)}/event/{event['id']}"}  # fmt: skip
