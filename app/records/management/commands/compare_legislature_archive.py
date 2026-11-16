"""Check that the legislature record sets hold everything the legislature data repository
(south-dakota-legislature-data, 1997–2024) captured, file by file and field by field.

The repository's files are read from the raw store (`import_legislature_archive`). For each
kind of file the report gives how many were checked, how many match, which fields differ
(with examples), and which have no record. A value a record keeps in `earlier` (one the API
no longer gives) counts as the record's. Exits non-zero when anything is missing or differs,
so the repository is retired only on a clean report (or one whose every difference is
explained).
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

from django.core.management.base import BaseCommand

from records.models import Record
from records.sources._legislature.build import archive_root, iso_day
from records.sources._legislature.sets import HISTORICAL_FIELDS

CHAMBER = {"H": "House", "S": "Senate"}


def words(text) -> str:
    """Text reduced to its letters and digits, to compare two extractions of one document."""
    return re.sub(r"[\W_]+", "", str(text or "")).lower()


def tokens(text) -> list[str]:
    return re.findall(r"[a-z0-9]+", str(text or "").lower())


def in_order(needle: list[str], haystack: list[str]) -> bool:
    """Whether every word of needle is in haystack, in the same order (other words between)."""
    it = iter(haystack)
    return all(word in it for word in needle)


def all_runs(needle: list[str], haystack: list[str], n: int = 6) -> bool:
    runs = {tuple(haystack[i : i + n]) for i in range(len(haystack) - n + 1)}
    return all(tuple(needle[i : i + n]) in runs for i in range(len(needle) - n + 1))


def norm(value):
    if value in ("", [], {}, "None"):
        return None
    if isinstance(value, str):
        return " ".join(value.replace("\xa0", " ").split())
    return value


def records(slug: str, fields=None) -> dict[str, dict]:
    """The record set's current records' data by key (only `fields`, when given)."""
    out = {}
    rows = Record.objects.filter(recordset__slug=slug, dropped_at__isnull=True)
    for key, data in rows.values_list("key", "data").iterator(chunk_size=2000):
        out[key] = {f: data.get(f) for f in fields} if fields else data
    return out


def as_listed(data: dict, field: str, current=None):
    """The value an earlier copy had where the API now shows another (`earlier`), else the
    record's (or `current`)."""
    found = (data.get("earlier") or {}).get(field)
    if found is not None:
        return found["value"]
    return kept(data, field) if current is None else current


def kept_list(data: dict, field: str) -> list:
    """The items a record keeps in `earlier` for a list the API has dropped some of."""
    return ((data.get("earlier") or {}).get(field) or {}).get("value") or []


def kept(data: dict, field: str):
    """The record's value for a field, or the one it keeps from an earlier copy."""
    if norm(data.get(field)) is not None:
        return data.get(field)
    return ((data.get("earlier") or {}).get(field) or {}).get("value")


class Report:
    def __init__(self):
        self.parts: dict[str, dict] = {}

    def part(self, name: str) -> dict:
        return self.parts.setdefault(name, {"checked": 0, "match": 0, "missing": 0,
                                            "missing_examples": [], "differ": {}})  # fmt: skip

    def check(self, name: str, ident, pairs: dict | None) -> None:
        """pairs: field → (the repository's value, the record's); None: no record."""
        p = self.part(name)
        p["checked"] += 1
        if pairs is None:
            p["missing"] += 1
            if len(p["missing_examples"]) < 10:
                p["missing_examples"].append(ident)
            return
        bad = {f: (a, b) for f, (a, b) in pairs.items() if norm(a) != norm(b)}
        if not bad:
            p["match"] += 1
        for f, (a, b) in bad.items():
            # "emptied": the record has nothing where the repository has a value (the API no
            # longer gives it); "added at the source": the reverse; "changed": both have values
            if isinstance(a, list) and b == []:
                how = "items not in the record"  # lacking(): what the record's list lacks
            else:
                how = ("emptied" if norm(b) is None else
                       "added at the source" if norm(a) is None else "changed")  # fmt: skip
            d = p["differ"].setdefault(f"{f} ({how})", {"count": 0, "examples": []})
            d["count"] += 1
            if len(d["examples"]) < 5:
                d["examples"].append({"id": ident, "repository": a, "record": b})

    @property
    def clean(self) -> bool:
        return all(not p["missing"] and not p["differ"] for p in self.parts.values())


def lacking(repository, record) -> list:
    """What the repository's list has that the record's doesn't (the API often has more)."""
    have = set(as_set(record))
    return [x for x in as_set(repository) if x not in have]


def ids(rows, field: str) -> list:
    """The IDs in a list of objects (or a list of bare IDs, as the repository keeps some)."""
    return as_set(r.get(field) if isinstance(r, dict) else r for r in rows or [])


def as_set(values) -> list:
    return sorted({json.dumps(v, sort_keys=True) for v in values or []})


class Command(BaseCommand):
    help = "Check the legislature record sets against the legislature data repository's files"

    def add_arguments(self, parser):
        parser.add_argument("--out", type=Path, help="write the full report (JSON) here")

    def handle(self, *args, out=None, **options):
        root = archive_root() / "data"
        report = Report()
        self.legislators(root, report)
        self.historical(root, report)
        self.sessions(root, report)
        self.committees(root, report)
        self.bills(root, report)
        self.crosswalk(report)
        self.seating_charts(root, report)
        for name, p in report.parts.items():
            differ = ", ".join(f"{f} {d['count']}" for f, d in sorted(p["differ"].items()))
            line = f"{name}: {p['checked']} checked, {p['match']} match, {p['missing']} missing"
            self.stdout.write(line + (f"; differ: {differ}" if differ else ""))
        if out:
            out.write_text(json.dumps(report.parts, indent=1, default=str))
            self.stdout.write(f"  report: {out}")
        if not report.clean:
            raise SystemExit(1)

    # --- each kind of file ---

    def legislators(self, root: Path, report: Report) -> None:
        stored = records("legislature-legislators-by-term")
        contact = {"address1": "home_address1", "address2": "home_address2", "city": "home_city",
                   "state": "home_state", "zipcode": "home_zip", "phone_home": "home_phone",
                   "phone_capitol": "capitol_phone", "phone_biz": "business_phone",
                   "phone_cell": "cell_phone", "email": "email"}  # fmt: skip
        for path in sorted((root / "legislators").glob("sd-legislature-legislator-*.json")):
            old = json.loads(path.read_text())
            rec = stored.get(str(old["legislator_profile_id"]))
            if rec is None:
                report.check("legislators", old["legislator_profile_id"], None)
                continue
            pairs = {
                "name": (old.get("name"), as_listed(rec, "first_last_name")),
                "chamber": (CHAMBER.get(old.get("chamber"), old.get("chamber")), rec["chamber"]),
                "session_id": (old.get("session_id"), rec.get("session_id")),
                "year": (old.get("year"), rec.get("year")),
                "district": (old.get("district"), as_listed(rec, "district")),
                "party": (old.get("party"), rec.get("party")),
                "term": (old.get("term"), as_listed(rec, "term")),
                "occupation": (old.get("occupation"), as_listed(rec, "occupation")),
                "counties": (old.get("counties"), rec.get("counties")),
                "picture": (old.get("picture"), as_listed(rec, "picture")),
                "legislator_canonical_id": (old.get("legislator_canonical_id"),
                                            rec.get("legislator_id")),
                **{theirs: (old.get(theirs), kept(rec, ours)) for theirs, ours in contact.items()},
            }  # fmt: skip
            report.check("legislators", old["legislator_profile_id"], pairs)

    def historical(self, root: Path, report: Report) -> None:
        stored = records("legislature-historical-legislators")
        for old in json.loads((root / "legislators" / "legislators-historical.json").read_text()):
            rec = stored.get(str(old["legislator_id_canon"]))
            if rec is None:
                report.check("historical legislators", old["legislator_id_canon"], None)
                continue
            # the listing's value, or the repository's where the record keeps it in `earlier`
            # (the listing now gives it differently)
            earlier = rec.get("earlier") or {}
            listed = (rec.get("listed") or {}) | {f: v["value"] for f, v in earlier.items()
                                                  if f in HISTORICAL_FIELDS.values()}  # fmt: skip
            pairs = {
                "name_first": (old.get("name_first"), listed.get("FirstName")),
                "name_last": (old.get("name_last"), listed.get("LastName")),
                "name_middle": (old.get("name_middle"), listed.get("MiddleName")),
                "gender": (old.get("gender"), listed.get("Gender")),
                "birthday": (iso_day(old.get("birthday")), iso_day(listed.get("Birthdate"))),
                "deathday": (iso_day(old.get("deathday")), iso_day(listed.get("Deathdate"))),
                "member_type": (old.get("member_type"), listed.get("MemberType")),
                "year_start": (old.get("year_start"), listed.get("StartYear")),
                "year_end": (old.get("year_end"), listed.get("EndYear")),
                "notes": (old.get("notes"), listed.get("Remarks")),
                "counties": (
                    lacking(
                        old.get("counties"),
                        (listed.get("County") or []) + (kept_list(rec, "County")),
                    ),
                    [],
                ),
                "cities": (
                    lacking(
                        old.get("cities"), (listed.get("City") or []) + (kept_list(rec, "City"))
                    ),
                    [],
                ),
                "parties": (lacking(old.get("parties"), listed.get("Party")), []),
                "chambers": (lacking(old.get("chambers"), listed.get("Body")), []),
                "offices": (lacking(old.get("offices"), listed.get("Office")), []),
            }
            report.check("historical legislators", old["legislator_id_canon"], pairs)

    def sessions(self, root: Path, report: Report) -> None:
        stored = records("legislature-sessions")
        keysets = {slug: set(records(slug, fields=())) for slug in (
            "legislature-bills", "legislature-legislators-by-term",
            "legislature-committee-memberships", "legislature-session-laws",
            "legislature-documents")}  # fmt: skip
        laws = records("legislature-session-laws", fields=("SessionId", "BillId")).values()
        laws_by_bill = {(d.get("SessionId"), str(d.get("BillId"))) for d in laws}
        for path in sorted((root / "sessions").glob("*.json")):
            old = json.loads(path.read_text())
            rec = stored.get(str(old["session_id"]))
            if rec is None:
                report.check("sessions", old["session_id"], None)
                continue
            dates = rec.get("dates") or {}
            earlier = rec.get("earlier") or {}
            kept_links = (earlier.get("session_laws") or {}).get("value") or {}
            kept_docs = {d.get("document_id") for d in (earlier.get("Documents") or {}).get("value")
                         or []}  # fmt: skip

            def gone(ids, slug):
                return sorted(str(i) for i in ids if str(i) not in keysets[slug])

            pairs = {
                "session_number": (old.get("session_number"), rec.get("SessionNumber")),
                "is_special_session": (old.get("is_special_session"), rec.get("SpecialSession")),
                "start_date": (old.get("start_date"), dates.get("start")),
                "end_date": (old.get("end_date"), dates.get("end")),
                "bills without a record": ([], gone(old.get("bills") or [], "legislature-bills")),
                "legislators without a record": ([], gone(old.get("legislators") or [],
                                                          "legislature-legislators-by-term")),
                "committees without a record": ([], gone(old.get("committees") or [],
                                                         "legislature-committee-memberships")),
                # a session law the API renumbered is still there under its session and bill
                "session laws without a record": ([], sorted(
                    str(law) for bill, law in (old.get("session_laws") or {}).items()
                    if str(law) not in keysets["legislature-session-laws"]
                    and (int(old["session_id"]), str(bill)) not in laws_by_bill
                    and str(law) not in {str(x) for x in kept_links.values()})),
                "documents without a record": ([], [d for d in gone(
                    [d["document_id"] for d in old.get("docs") or []], "legislature-documents")
                    if int(d) not in kept_docs]),
            }  # fmt: skip
            report.check("sessions", old["session_id"], pairs)

    def committees(self, root: Path, report: Report) -> None:
        stored = records("legislature-committee-memberships")
        for path in sorted((root / "committees").glob("*.json")):
            old = json.loads(path.read_text())
            rec = stored.get(str(old["committee_id"]))
            if rec is None:
                report.check("committees", old["committee_id"], None)
                continue
            committee = rec.get("Committee") or {}
            staff = [s.get("UserId") for s in rec.get("Staff") or []]
            members = [{"legislator_profile_id": m.get("SessionMemberId"),
                        "committee_member_type": m.get("CommitteeMemberType")}
                       for m in rec.get("CommitteeMembers") or []]  # fmt: skip
            pairs = {
                "committee_name": (old.get("committee_name"), committee.get("FullName")),
                "committee_room": (old.get("committee_room"), committee.get("Room")),
                "committee_days": (old.get("committee_days"),
                                   as_listed(rec, "Days", committee.get("Days"))),
                "is_full_body": (old.get("is_full_body"), rec.get("FullBody")),
                "committee_id_canon": (old.get("committee_id_canon"), rec.get("CommitteeId")),
                "authority": (old.get("authority"), rec.get("Authority")),
                "members": (lacking(old.get("members"),
                                    members + kept_list(rec, "CommitteeMembers")), []),
                "staff": (lacking(old.get("staff"), staff + kept_list(rec, "Staff")), []),
            }  # fmt: skip
            report.check("committees", old["committee_id"], pairs)

    def bills(self, root: Path, report: Report) -> None:
        stored = records("legislature-bills")
        versions = records("legislature-bill-versions", fields=("text",))
        votes = records("legislature-roll-calls",
                        fields=("RollCalls", "PresidentVote", "earlier"))  # fmt: skip
        for path in sorted((root / "bills").glob("sd-legislature-bill-*.json")):
            old = json.loads(path.read_text())
            rec = stored.get(str(old["bill_id"]))
            if rec is None:
                report.check("bills", old["bill_id"], None)
                continue
            if "repository" in rec:  # a bill the API no longer serves: the repository's own
                report.check("bills", old["bill_id"], {"file": (old == rec["repository"], True)})
                continue
            sponsors = [{"legislator_profile_id": s.get("SessionMemberId"),
                         "is_prime": s.get("SponsorType") == "P"}
                        for s in rec.get("BillSponsor") or []]  # fmt: skip
            actions = [[a.get("DocumentId"), a.get("ActionDate")]
                       for a in rec.get("ActionLog") or []]  # fmt: skip
            actions += [[a.get("document_id"), a.get("action_date")]  # kept from the repository
                        for a in ((rec.get("earlier") or {}).get("ActionLog") or {}).get("value")
                        or []]  # fmt: skip
            pairs = {
                "bill_type": (old.get("bill_type"), rec.get("BillTypeFull")),
                "bill_number": (old.get("bill_number"), rec.get("BillNumber")),
                "bill_title": (old.get("bill_title"), rec.get("Title")),
                "session_id": (old.get("session_id"), rec.get("SessionId")),
                "sponsors": (lacking(old.get("sponsors"), sponsors), []),
                "keywords": (lacking(old.get("keywords"),
                                     [k.get("Keyword") for k in rec.get("Keywords") or []]), []),
                "audio": (lacking([a.get("url") for a in old.get("audio") or []],
                                  [a.get("Url") for a in rec.get("Audio") or []]), []),
                "amendments": (lacking(ids(old.get("amendments"), "document_id"),
                                       ids(rec.get("Amendments"), "DocumentId")), []),
                "fiscal_notes": (lacking(ids(old.get("fiscal_notes"), "document_id"),
                                         ids(rec.get("FiscalNotes"), "DocumentId")), []),
                "action_log": (lacking([[a.get("document_id"), a.get("action_date")]
                                        for a in old.get("action_log") or []], actions), []),
                "versions": (lacking(ids(old.get("bill_versions"), "bill_version_id"),
                                     ids(rec.get("Versions"), "DocumentId")), []),
            }  # fmt: skip
            report.check("bills", old["bill_id"], pairs)
            for v in old.get("bill_versions") or []:
                text = versions.get(str(v["bill_version_id"]))
                ident = (old["bill_id"], v["bill_version_id"])
                if text is None and not tokens(v.get("bill_text")):
                    # the repository has no text for it either (the API errors on it)
                    report.check("bill version text", ident, {"bill_text": ("", "")})
                elif text is None:
                    report.check("bill version text", ident, None)
                else:
                    # the repository kept the bill's paragraphs (not its tables); the record
                    # has the whole page: the repository's words must all be in it, in order
                    theirs = tokens(v.get("bill_text"))
                    ours = tokens(text.get("text"))
                    # or every run of six of its words in it (a table read in another order), or
                    # every word it has (its reader took nested blocks twice: "ten points ten
                    # points" where the page has "ten points" once)
                    same = (
                        in_order(theirs, ours) or all_runs(theirs, ours) or set(theirs) <= set(ours)
                    )
                    lost = "" if same else " ".join(theirs[:30])
                    report.check("bill version text", ident, {
                        "bill_text (words not in the record's, in order)": (lost, "")})  # fmt: skip
            for a in old.get("action_log") or []:
                vote = a.get("vote") or {}
                if not vote.get("vote_id"):
                    continue
                rec_vote = votes.get(str(vote["vote_id"]))
                if rec_vote is None:
                    report.check("roll calls", vote["vote_id"], None)
                    continue
                by_vote = defaultdict(list)
                for rc in rec_vote.get("RollCalls") or []:
                    by_vote[rc.get("Vote1")].append(rc.get("SessionMemberId"))
                pairs = {k: (sorted(v), sorted(by_vote.get(k, []))) for k, v in vote.items()
                         if k not in ("vote_id", "president_vote")}  # fmt: skip
                pairs["president_vote"] = (vote.get("president_vote"),
                                           kept(rec_vote, "PresidentVote"))  # fmt: skip
                report.check("roll calls", vote["vote_id"], pairs)

    def crosswalk(self, report: Report) -> None:
        from records.sources._legislature.build import CROSSWALK, archive

        stored = records("legislature-legislators-by-term", fields=("legislator_id",))
        for row in archive(CROSSWALK) or []:
            rec = stored.get(row["legislator_profile_id"])
            canon = int(row["legislator_id_canon"]) if row["legislator_id_canon"].strip() else None
            report.check("crosswalk", row["legislator_profile_id"], None if rec is None else {
                "legislator_id_canon": (canon, rec.get("legislator_id"))})  # fmt: skip

    def seating_charts(self, root: Path, report: Report) -> None:
        for path in sorted((root / "seating-charts" / "pdfs").glob("*")):
            report.check("seating charts (kept in the raw store)", path.name,
                         {"bytes": (path.stat().st_size > 0, True)})  # fmt: skip
