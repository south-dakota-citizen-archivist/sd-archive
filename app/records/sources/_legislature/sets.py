"""The legislature's record sets besides legislators by session, built from the crawl's
stored API responses (`build.api`). Each record is the API's own object for one thing, as
served (its field names and values), with the responses that hang off it alongside, keyed by
the API's ID for it:

- sessions (SessionId): `Sessions/{id}` and its documents (`Sessions/General/{id}`), with the
  start and end dates the legislature data repository recorded by hand.
- historical legislators (MemberId): `Historical/Member/{id}`, every service, office and
  Blue Book page; the Historical Listing's ID is the legislator's stable ID.
- bills (BillId): `Bills/{id}` with its versions, action log, amendments, fiscal notes,
  audio index and conference committees.
- bill versions (DocumentId): the version's text (`Bills/HTML/{id}`), the HTML as served (it
  marks words struck from the law and added to it) and as plain text.
- roll calls (VoteId), session committees (SessionCommitteeId), interim committees
  (InterimYearCommitteeId), session laws (SessionLawId), staff (PersonId).
- documents (DocumentId): every document the API lists by type, the listing's row (the
  files aren't kept).
- statutes (the section number, with the constitution) and administrative rules (the rule
  number): a title, chapter or article's HTML repeats the text of everything under it, so it's
  kept only for nodes with nothing under them (`text_in_children`); the raw store has it all.

People in roll calls, sponsor lists and committee rosters are named by their session profile
(SessionMemberId), which legislators by session ties to the legislator, so they aren't
mentioned again here.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from functools import cache

from records.sources import Found, Item

from .build import (
    SITE,
    STATE,
    api,
    api_url,
    archive,
    archive_root,
    archive_url,
    empty,
    iso_day,
    keep_earlier,
)
from .crawl import raw_root


def _ids(path: str, key: str) -> list:
    return [x[key] for x in api(path) or [] if isinstance(x, dict) and key in x]


def session_ids() -> list[int]:
    return sorted(_ids("Sessions", "SessionId"))


def _stored(folder: str) -> list[str]:
    """The names (without .json) of the stored responses in an API folder."""
    root = raw_root() / "api" / folder
    return sorted(p.stem for p in root.glob("*.json")) if root.exists() else []


def _found(items: Iterator[Item], **checks) -> Found:
    out = Found(items)
    out.checks = {"records": len(out), **checks}
    return out


def legacy_id(record) -> str:
    """The earlier system keyed by the public page (…/Session/Bill/26699,
    …/Session/Committee/1266/Detail, …/Staff/Profile/44): the ID in it."""
    m = re.search(r"/(\d+)(?:/Detail)?/?$", record.key)
    return m.group(1) if m else record.key


def html_text(html: str | None) -> str:
    """The words of an HTML fragment, whitespace closed up."""
    if not html or not html.strip():
        return ""
    import lxml.html

    return " ".join(lxml.html.fromstring(html).text_content().split())


# --- sessions ---


def keep_dropped_session_links(data: dict, sid: int) -> None:
    """What the repository's session file has that the API no longer does, in `earlier`:
    its bill → session law links when a law it names is gone (the API renumbered session
    52's laws and lost their bills), and documents no listing has now."""
    name = f"data/sessions/sd-legislature-session-{sid}.json"
    old = archive(name)
    if not old:
        return
    earlier = {}
    laws = {str(x["SessionLawId"]) for x in api(f"SessionLaws/{sid}") or []}
    links = old.get("session_laws") or {}
    if any(str(law) not in laws for law in links.values()):
        earlier["session_laws"] = {"value": links, "from": archive_url(name)}
    listed = _listed_documents()
    gone = [d for d in old.get("docs") or [] if d.get("document_id") not in listed]
    if gone:
        earlier["Documents"] = {"value": gone, "from": archive_url(name)}
    if earlier:
        data["earlier"] = earlier


def sessions(fetcher=None) -> Found:
    dates = archive("crawler/session-dates.json") or {}
    url = archive_url("crawler/session-dates.json")

    def build():
        for sid in session_ids():
            data = dict(api(f"Sessions/{sid}") or {"SessionId": sid})
            data["Documents"] = api(f"Sessions/General/{sid}") or []
            if when := dates.get(str(sid)):
                data["dates"] = {"start": when.get("start_date"), "end": when.get("end_date"),
                                 "from": url}  # fmt: skip
            keep_dropped_session_links(data, sid)
            yield Item(key=str(sid), data=data, source_url=api_url(f"Sessions/{sid}"),
                       places=[STATE])  # fmt: skip

    return _found(build())


# --- historical legislators ---


def _unsigned(url: str | None) -> str | None:
    """A stored file's link without its expiring signature (it changes with every visit)."""
    return url.split("?", 1)[0] if url else url


HISTORICAL = "data/legislators/legislators-historical.json"


def keep_dropped_places(data: dict, old: dict | None) -> None:
    """Counties and cities the repository's Historical Listing had for the legislator that
    the listing no longer does, in `earlier`."""
    listed = data["listed"]
    for theirs, ours in (("counties", "County"), ("cities", "City")):
        gone = [x for x in (old or {}).get(theirs) or [] if x not in (listed.get(ours) or [])]
        if gone:
            data.setdefault("earlier", {})[ours] = {"value": gone, "from": archive_url(HISTORICAL)}


# the repository's Historical Listing field → the listing's
HISTORICAL_FIELDS = {
    "name_first": "FirstName", "name_last": "LastName", "name_middle": "MiddleName",
    "gender": "Gender", "birthday": "Birthdate", "deathday": "Deathdate",
    "member_type": "MemberType", "year_start": "StartYear", "year_end": "EndYear",
    "notes": "Remarks",
}  # fmt: skip
DAYS = ("Birthdate", "Deathdate")


def keep_changed_listing(data: dict, old: dict | None) -> None:
    """The repository's value for each listing field the listing now gives differently or
    no longer gives (a corrected spelling or date; an end year a returning legislator no
    longer has), in `earlier` as the repository wrote it. The record keeps the listing's."""
    listed = data["listed"]
    for theirs, ours in HISTORICAL_FIELDS.items():
        value = (old or {}).get(theirs)
        if empty(value):
            continue
        now = listed.get(ours)
        if ours in DAYS:
            same = iso_day(value) == iso_day(now)
        else:
            same = not empty(now) and " ".join(str(value).split()) == " ".join(str(now).split())
        if not same:
            data.setdefault("earlier", {})[ours] = {"value": value,
                                                    "from": archive_url(HISTORICAL)}  # fmt: skip
    if data.get("earlier"):
        data["earlier"] = dict(sorted(data["earlier"].items()))


def historical_legislators(fetcher=None) -> Found:
    listed = {m["MemberId"]: m for m in api("Historical/AllFlatMembers") or []}
    repository = {m["legislator_id_canon"]: m for m in archive(HISTORICAL) or []}
    missing = []

    def build():
        for mid, flat in sorted(listed.items()):
            detail = api(f"Historical/Member/{mid}")
            if detail is None:
                missing.append(mid)
            data = dict(detail or {}) | {"listed": flat}
            keep_dropped_places(data, repository.get(mid))
            keep_changed_listing(data, repository.get(mid))
            for pic in data.get("Pictures") or []:
                pic["Url"] = _unsigned(pic.get("Url"))
            yield Item(key=str(mid), data=data, places=[STATE],
                       source_url=f"{SITE}Historical/Legislator/Profile/{mid}")  # fmt: skip

    items = list(build())
    return _found(iter(items), without_detail=len(missing))


def historical_mentions(record) -> list:
    from entities.mentions import from_text

    d = record.data
    listed = d.get("listed") or {}
    name = " ".join(str(listed.get("Name") or "").replace("\xa0", " ").split())
    if not name:
        name = f"{d.get('LastName')}, {d.get('FirstName')} {d.get('MiddleName') or ''}".strip()
    context = {"served": f"{listed.get('StartYear')}–{listed.get('EndYear') or ''}",
               "body": listed.get("Body"), "party": listed.get("Party"),
               "county": listed.get("County"), "city": listed.get("City")}  # fmt: skip
    return from_text(name, dataset=record.recordset.dataset, record_key=str(record),
                     field="name", role="legislator", order="family_first",
                     url=api_url(f"Historical/Member/{record.key}"),
                     context={k: v for k, v in context.items() if v},
                     gold_entity_id=f"sd-legislator:{record.key}")  # fmt: skip


# --- bills and their versions ---

PER_BILL = {
    "Versions": "Bills/Versions/{}", "ActionLog": "Bills/ActionLog/{}",
    "Amendments": "Bills/Amendments/{}", "FiscalNotes": "Bills/FiscalNotes/{}",
    "Audio": "Bills/Audio/{}", "ConferenceCommittees": "ConferenceCommittees/Bill/{}",
}  # fmt: skip


def bill_ids() -> list[int]:
    out = set()
    for sid in session_ids():
        out |= set(_ids(f"Bills/Session/{sid}", "BillId"))
    return sorted(out)


def keep_dropped_actions(data: dict) -> None:
    """Action log entries the repository kept that the API no longer lists (by document and
    date), in `earlier` as the repository wrote them."""
    name = f"data/bills/sd-legislature-bill-{data['BillId']}.json"
    old = archive(name)
    if not old:
        return
    listed = {(a.get("DocumentId"), a.get("ActionDate")) for a in data.get("ActionLog") or []}
    dropped = [a for a in old.get("action_log") or []
               if (a.get("document_id"), a.get("action_date")) not in listed]  # fmt: skip
    if dropped:
        earlier = data.setdefault("earlier", {})
        earlier["ActionLog"] = {"value": dropped, "from": archive_url(name)}


def bills(fetcher=None) -> Found:
    missing = []

    def build():
        for bid in bill_ids():
            data = api(f"Bills/{bid}")
            if data is None:
                missing.append(bid)
                continue
            data = dict(data)
            for name, path in PER_BILL.items():
                data[name] = api(path.format(bid)) or []
            keep_dropped_actions(data)
            yield Item(key=str(bid), data=data, source_url=f"{SITE}Session/Bill/{bid}",
                       places=[STATE])  # fmt: skip

    items = list(build())
    served = {i.key for i in items}
    only_in_repository = 0
    for path in sorted((archive_root() / "data" / "bills").glob("sd-legislature-bill-*.json")):
        if path.stem.rsplit("-", 1)[1] in served:
            continue
        name = f"data/bills/{path.name}"
        old = archive(name)
        only_in_repository += 1
        data = {"BillId": old["bill_id"], "BillTypeFull": old.get("bill_type"),
                "BillNumber": old.get("bill_number"), "Title": old.get("bill_title"),
                "SessionId": old.get("session_id"), "repository": old,
                "from": archive_url(name)}  # fmt: skip
        items.append(Item(key=str(old["bill_id"]), data=data, source_url=archive_url(name),
                          places=[STATE]))  # fmt: skip
    return _found(iter(items), listed_without_bill=len(missing),
                  only_in_repository=only_in_repository)  # fmt: skip


def bill_versions(fetcher=None) -> Found:
    """Every version of every bill whose text is stored (the rest wait for the crawl)."""
    waiting = 0

    def build():
        nonlocal waiting
        for bid in bill_ids():
            for v in api(f"Bills/Versions/{bid}") or []:
                doc = api(f"Bills/HTML/{v['DocumentId']}")
                if doc is None:
                    waiting += 1
                    continue
                html = doc.get("DocumentHtml") or ""
                data = {**doc, "BillVersion": v.get("BillVersion"),
                        "DocumentDate": v.get("DocumentDate"), "text": html_text(html)}  # fmt: skip
                yield Item(key=str(v["DocumentId"]), data=data, places=[STATE],
                           source_url=f"https://mylrc.sdlegislature.gov/api/Documents/"
                                      f"{v['DocumentId']}.pdf")  # fmt: skip

    items = list(build())
    return _found(iter(items), text_not_yet_stored=waiting)


# --- roll calls, committees, session laws, staff ---


@cache
def repository_votes() -> dict[str, tuple[dict, str]]:
    """Each roll call the legislature data repository kept (in its bills' action logs): the
    presiding officer's vote, and the file it's in."""
    out = {}
    for path in sorted((archive_root() / "data" / "bills").glob("sd-legislature-bill-*.json")):
        for action in (archive(f"data/bills/{path.name}") or {}).get("action_log") or []:
            vote = action.get("vote") or {}
            if vote.get("vote_id"):
                out[str(vote["vote_id"])] = ({"PresidentVote": vote.get("president_vote")},
                                             archive_url(f"data/bills/{path.name}"))  # fmt: skip
    return out


def roll_calls(fetcher=None) -> Found:
    """Roll calls; the presiding officer's vote the API now leaves empty is kept from the
    repository (`earlier`)."""
    earlier = 0

    def build():
        nonlocal earlier
        repo = repository_votes()
        for vid in _stored("Votes"):
            data = api(f"Votes/{vid}")
            if data:
                keep_earlier(data, ["PresidentVote"], repo.get(vid, (None, "")))
                earlier += "earlier" in data
                yield Item(key=vid, data=data, source_url=api_url(f"Votes/{vid}"),
                           places=[STATE])  # fmt: skip

    items = list(build())
    return _found(iter(items), earlier=earlier)


PER_COMMITTEE = {
    "Bills": "SessionCommittees/Bills/{}", "Agendas": "SessionCommittees/CommitteeAgendas/{}",
    "Documents": "SessionCommittees/Documents/{}?Type=2&Type=4&Type=3&Type=5",
    "HasVideos": "SessionCommittees/HasVideos/{}",
}  # fmt: skip
PER_INTERIM = {"Documents": "InterimCommittees/Documents/{}?Type=2&Type=4&Type=3&Type=5",
               "HasVideos": "InterimCommittees/HasVideos/{}"}  # fmt: skip


def keep_dropped_members(data: dict, cid: int) -> None:
    """Members and staff the repository's committee file lists that the API no longer does,
    and the meeting days it had where the API now differs, in `earlier` as the repository
    wrote them."""
    name = f"data/committees/sd-legislature-committee-{cid}.json"
    old = archive(name)
    if not old:
        return
    members = {(m.get("SessionMemberId"), m.get("CommitteeMemberType"))
               for m in data.get("CommitteeMembers") or []}  # fmt: skip
    staff = {s.get("UserId") for s in data.get("Staff") or []}
    dropped = {
        "CommitteeMembers": [m for m in old.get("members") or [] if
                             (m.get("legislator_profile_id"), m.get("committee_member_type"))
                             not in members],
        "Staff": [u for u in old.get("staff") or [] if u not in staff],
    }  # fmt: skip
    for field, value in dropped.items():
        if value:
            data.setdefault("earlier", {})[field] = {"value": value, "from": archive_url(name)}
    # the API shows a committee's current meeting days for every session it met
    days = (data.get("Committee") or {}).get("Days")
    if old.get("committee_days") and days and old["committee_days"] != days:
        data.setdefault("earlier", {})["Days"] = {"value": old["committee_days"],
                                                  "from": archive_url(name)}  # fmt: skip


def _committees(listing: str, key: str, detail: str, extra: dict, page: str,
                repository: bool = False) -> Found:  # fmt: skip
    def build():
        seen = set()
        for sid in session_ids():
            for cid in _ids(listing.format(sid), key):
                if cid in seen:
                    continue
                seen.add(cid)
                data = dict(api(detail.format(cid)) or {key: cid})
                for name, path in extra.items():
                    data[name] = api(path.format(cid))
                if repository:
                    keep_dropped_members(data, cid)
                yield Item(key=str(cid), data=data, places=[STATE],
                           source_url=SITE + page.format(cid))  # fmt: skip

    return _found(build())


def committees(fetcher=None) -> Found:
    return _committees("SessionCommittees/Session/{}", "SessionCommitteeId",
                       "SessionCommittees/Detail/{}", PER_COMMITTEE,
                       "Session/Committee/{}/Detail", repository=True)  # fmt: skip


def interim_committees(fetcher=None) -> Found:
    return _committees("InterimCommittees/Session/{}", "InterimYearCommitteeId",
                       "InterimCommittees/Detail/{}", PER_INTERIM,
                       "Interim/Committee/{}")  # fmt: skip


def session_laws(fetcher=None) -> Found:
    without_text = 0

    def build():
        nonlocal without_text
        for sid in session_ids():
            for row in api(f"SessionLaws/{sid}") or []:
                path = f"SessionLaws/SessionLaw/{row['SessionLawId']}"
                law = api(path)
                without_text += law is None
                data = {**row, **(law or {})}
                data["text"] = html_text(data.get("Html"))
                page = f"{SITE}Statutes/Session_Laws/Chapter/{row['SessionLawId']}"
                yield Item(key=str(row["SessionLawId"]), data=data, places=[STATE],
                           source_url=page)  # fmt: skip

    items = list(build())
    return _found(iter(items), without_text=without_text)


def staff(fetcher=None) -> Found:
    def build():
        for row in api("Users") or []:
            data = {**row, **(api(f"Users/{row['UserId']}") or {})}
            yield Item(key=str(row["PersonId"]), data=data, places=[STATE],
                       source_url=f"{SITE}Staff/Profile/{row['UserId']}")  # fmt: skip

    return _found(build())


def staff_mentions(record) -> list:
    from entities.mentions import from_text

    d = record.data
    return from_text(d.get("Name") or f"{d.get('FirstName')} {d.get('LastName')}",
                     dataset=record.recordset.dataset, record_key=str(record), field="name",
                     role="legislative staff", order="given_first",
                     url=api_url(f"Users/{d.get('UserId')}"),
                     context={"title": d.get("Title"), "group": d.get("StaffGroup1")})  # fmt: skip


# --- documents ---


def document_url(row: dict) -> str:
    """Where the document's file is served: by ID, with its file's extension (PDF if none)."""
    ext = (row.get("WebFilename") or "").rsplit(".", 1)[-1] if row.get("WebFilename") else ""
    return f"https://mylrc.sdlegislature.gov/api/Documents/{row['DocumentId']}.{ext or 'pdf'}"


def _document_rows() -> dict[int, dict]:
    rows: dict[int, dict] = {}
    root = raw_root() / "api" / "Documents"
    names = sorted(p.stem for p in root.glob("DocumentType__*.json")) if root.exists() else []
    names += [f"DocumentType/{p.stem}" for p in sorted((root / "DocumentType").glob("*.json"))
              if root.exists()]  # fmt: skip
    for name in names:
        for row in api(f"Documents/{name}") or []:
            if isinstance(row, dict) and row.get("DocumentId") is not None:
                rows.setdefault(row["DocumentId"], row)
    return rows


def _listed_documents() -> set[int]:
    return set(_document_rows())


def documents(fetcher=None) -> Found:
    """Every document the API lists, by type (agendas, minutes, journals, amendments, fiscal
    notes, reports, session documents…): the listing's row for it. The files aren't kept."""
    rows = _document_rows()

    def build():
        for did, row in sorted(rows.items()):
            yield Item(key=str(did), data=row, source_url=document_url(row), places=[STATE])

    return _found(build())


# --- statutes and rules ---


def _has_children(numbers: set[str], sep: str):
    prefixes = set()
    for n in numbers:
        parts = n.split(sep)
        for i in range(1, len(parts)):
            prefixes.add(sep.join(parts[:i]))
    return prefixes


def _tree(folder: str, number_field: str, sep: str, url) -> Iterator[Item]:
    nodes = {}
    for name in _stored(folder):
        node = api(f"{folder}/{name}")
        if isinstance(node, dict) and node.get(number_field):
            nodes[node[number_field]] = node
    parents = _has_children(set(nodes), sep)
    for number, node in sorted(nodes.items()):
        data = dict(node)
        if number in parents and data.get("Html"):
            data.pop("Html")
            data["text_in_children"] = True  # the HTML repeats its sections' text
        else:
            data["text"] = html_text(data.get("Html"))
        yield Item(key=number, data=data, source_url=url(number), places=[STATE])


def statutes(fetcher=None) -> Found:
    def build():
        yield from _tree("Statutes/Statute", "Statute", "-",
                         lambda n: f"{SITE}Statutes/{n}")  # fmt: skip
        yield from _tree("Statutes/Constitution", "Statute", "-",
                         lambda n: f"{SITE}Constitution/{n.removeprefix('0N-')}")  # fmt: skip

    return _found(build())


def rules(fetcher=None) -> Found:
    return _found(_tree("Rules", "RuleNumber", ":", lambda n: f"{SITE}Rules/Administrative/{n}"))


# --- the earlier system's records, for comparing ---


def _as(kind, value):
    if value in (None, "", "None"):
        return None
    if kind is bool:
        return value in (True, "True", "true")
    try:
        return kind(value)
    except (TypeError, ValueError):
        return value


def legacy_fields(mapping: dict[str, tuple[str, type]]):
    """from_legacy for a record set: the earlier system's fields (stored as text) under the
    API's names. mapping: its field → (the API's field, type)."""

    def from_legacy(data: dict) -> dict:
        return {ours: _as(kind, data.get(theirs)) for theirs, (ours, kind) in mapping.items()}

    return from_legacy
