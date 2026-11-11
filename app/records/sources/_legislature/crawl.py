"""A crawl of everything sdlegislature.gov's API serves as JSON, kept raw.

The API is undocumented; its routes come from the site's own JavaScript (the list is in
ROUTES below, grouped by what they hang off). The crawl starts at the roots (sessions,
statute titles, rules, historical lists, staff) and follows the IDs each response gives:
sessions → bills, members, committees → votes; statutes and rules by their Next links.

Every response is stored as served, once per URL, under media/raw/legislature/api/…, so the
crawl resumes where it stopped and the record sets are built from the store without
fetching again. Responses that change (the current session, the top-level lists) are
fetched again when the stored copy is older than REFRESH; earlier sessions are kept. A URL
that answers 404 is logged in missing.jsonl and not asked again.

Document files (PDF, HTML, Word) and meeting audio aren't fetched: the JSON lists them
(document IDs, audio links and timestamps), and the files wait for a decision on storage.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

API = "https://sdlegislature.gov/api/"
REFRESH = 20 * 3600  # seconds: refetch changing responses older than this
DOCUMENT_TYPES = range(1, 121)  # the site uses types up to 95; the sweep goes a little past

# Responses that don't depend on a session: the roots
ROOTS = [
    "Sessions", "Sessions/Current", "BillNumbers/BillTypes", "DistrictCounties",
    "Statutes/Title", "Statutes/Constitution", "Statutes/LastStatuesEffectiveDate", "Rules",
    "Tags", "Users", "Users/BudgetAgencies", "Users/RuleAgencies", "UserCommittees",
    "UserSubjectAreas", "Historical/AllFlatMembers", "Historical/Downloads",
    "Historical/Political", "Historical/Statehood", "Historical/Territorial",
    "Historical/Office/1", "Historical/Office/2", "Historical/Office/3", "Historical/Office/5",
    "Historical/Office/6", "Historical/Tenure/10/14", "Historical/Tenure/15/19",
    "Historical/Tenure/20/24", "Historical/Tenure/TwentyFive", "Housing/Types",
    "Documents/Menu", "Documents/FullMenu", "Documents/SideMenu", "Documents/Schedule?all=true",
    "Documents/LettersOfIntent", "Documents/DocumentType/UniqueSessions?Type=53",
    "SessionLaws/Book",
]  # fmt: skip

# Per session ({s} = SessionId)
PER_SESSION = [
    "Sessions/{s}", "Sessions/General/{s}", "Bills/Session/{s}", "Bills/BillStatus/{s}",
    "Bills/PrisonJail/{s}", "SessionMembers/Session/{s}", "SessionCommittees/Session/{s}",
    "InterimCommittees/Session/{s}", "ConferenceCommittees/Session/{s}", "SessionLaws/{s}",
    "BillLogs/SignedByGovernor/{s}", "BillLogs/VetoedByGovernor/{s}",
    "BillLogs/BillsOnGovernorsDesk/{s}", "BillLogs/Hoghouse/{s}", "BillLogs/Emergency/{s}",
    "BillLogs/Withdrawn/{s}", "Agencies/Session/{s}", "Keywords?SessionId={s}",
    "Documents/DocumentTypeHearings?SessionIds={s}", "DocumentHearings/HasDocuments?SessionIds={s}",
    "Documents/Menu?sessionId={s}", "Documents/DocumentType?Type=16&SessionIds={s}",
    "Documents/DocumentType?Type=72&Type=73&Type=74&SessionIds={s}",
]  # fmt: skip

PER_BILL = ["Bills/{b}", "Bills/Versions/{b}", "Bills/ActionLog/{b}", "Bills/Amendments/{b}",
            "Bills/FiscalNotes/{b}", "Bills/Audio/{b}", "Bills/HTML/{b}",
            "ConferenceCommittees/Bill/{b}"]  # fmt: skip
PER_MEMBER = ["SessionMembers/Detail/{m}", "SessionMembers/Bills/{m}",
              "SessionMembers/Committees/{m}", "SessionMembers/Hearing/{m}"]  # fmt: skip
PER_COMMITTEE = ["SessionCommittees/{c}", "SessionCommittees/Detail/{c}",
                 "SessionCommittees/Bills/{c}", "SessionCommittees/ActiveBills/{c}",
                 "SessionCommittees/InactiveBills/{c}", "SessionCommittees/CommitteeAgendas/{c}",
                 "SessionCommittees/Documents/{c}?Type=2&Type=4&Type=3&Type=5",
                 "SessionCommittees/HasVideos/{c}"]  # fmt: skip
PER_INTERIM = ["InterimCommittees/{i}", "InterimCommittees/Detail/{i}",
               "InterimCommittees/Documents/{i}?Type=2&Type=4&Type=3&Type=5",
               "InterimCommittees/HasVideos/{i}"]  # fmt: skip


def store_name(path: str) -> str:
    """Where a path's response is stored: api/<path>[__<query>].json, filename-safe."""
    base, _, query = path.partition("?")
    name = re.sub(r"[^A-Za-z0-9._/-]", "_", base)
    if query:
        name += "__" + re.sub(r"[^A-Za-z0-9._-]", "_", query)
    return f"api/{name}.json"


class Crawl:
    def __init__(self, fetcher, *, now: float | None = None):
        self.fetcher, self.store = fetcher, fetcher.store
        self.now = now or time.time()
        self.missing_path = self.store.root / "missing.jsonl"
        self.missing = set()
        if self.missing_path.exists():
            lines = self.missing_path.read_text().splitlines()
            self.missing = {json.loads(x)["path"] for x in lines if x.strip()}
        self.fetched = self.reused = 0

    def get(self, path: str, *, changing: bool = False):
        """The JSON at path: stored, or fetched and stored. None for a 404 or an empty body."""
        if path in self.missing and not changing:
            return None
        name = store_name(path)
        stored = self.store.root / name
        refresh = changing and stored.exists() and self.now - stored.stat().st_mtime > REFRESH
        if stored.exists() and not refresh:
            self.reused += 1
            body = stored.read_bytes()
        else:
            try:
                body = self.fetcher.fetch(API + path, name, refresh=refresh, allow_empty=True,
                                          headers={"Accept": "application/json"})  # fmt: skip
            except requests.HTTPError as e:
                status = getattr(e.response, "status_code", None)
                if status in (404, 500):  # the API answers 500 for some IDs it doesn't have
                    self._mark_missing(path, status)
                    return None
                raise
            self.fetched += 1
            if self.fetched % 500 == 0:
                logger.info("legislature: %d fetched, %d reused", self.fetched, self.reused)
        if not body.strip():
            return None
        try:
            return json.loads(body)
        except ValueError:
            return None  # an HTML answer (an error page); kept in the store for a look

    def _mark_missing(self, path: str, status: int | None) -> None:
        self.missing.add(path)
        with self.missing_path.open("a") as f:
            f.write(json.dumps({"path": path, "status": status}) + "\n")

    # --- the walk ---

    STEPS = ("roots", "sessions", "statutes", "rules", "historical", "staff", "documents")

    def run(self, steps: list[str] | None = None, sessions: list[int] | None = None) -> dict:
        for step in steps or self.STEPS:
            logger.info("legislature: step %s", step)
            if step == "sessions":
                self.step_sessions(sessions)
            else:
                getattr(self, f"step_{step}")()
        return {"fetched": self.fetched, "reused": self.reused, "missing": len(self.missing)}

    def step_roots(self) -> None:
        for path in ROOTS:
            self.get(path, changing=True)

    def session_ids(self) -> list[tuple[int, bool]]:
        """(SessionId, current) for every session, newest first."""
        found = self.get("Sessions", changing=True) or []
        return sorted(((s["SessionId"], bool(s.get("CurrentSession"))) for s in found),
                      key=lambda x: -x[0])  # fmt: skip

    def step_sessions(self, only: list[int] | None = None) -> None:
        for sid, current in self.session_ids():
            if only and sid not in only:
                continue
            for path in PER_SESSION:
                self.get(path.format(s=sid), changing=current)
            bills = self.get(f"Bills/Session/{sid}", changing=current) or []
            for bill in bills:
                for path in PER_BILL:
                    self.get(path.format(b=bill["BillId"]), changing=current)
                for vote_id in self.vote_ids(bill["BillId"], current):
                    self.get(f"Votes/{vote_id}", changing=current)
            for m in self.get(f"SessionMembers/Session/{sid}", changing=current) or []:
                for path in PER_MEMBER:
                    self.get(path.format(m=m["SessionMemberId"]), changing=current)
            for c in self.get(f"SessionCommittees/Session/{sid}", changing=current) or []:
                for path in PER_COMMITTEE:
                    self.get(path.format(c=c["SessionCommitteeId"]), changing=current)
            for i in self.get(f"InterimCommittees/Session/{sid}", changing=current) or []:
                for path in PER_INTERIM:
                    self.get(path.format(i=i["InterimYearCommitteeId"]), changing=current)
            for law in self.get(f"SessionLaws/{sid}", changing=current) or []:
                self.get(f"SessionLaws/SessionLaw/{law['SessionLawId']}", changing=current)
            logger.info("legislature: session %s done (%d fetched so far)", sid, self.fetched)

    def vote_ids(self, bill_id: int, current: bool) -> list[int]:
        log = self.get(f"Bills/ActionLog/{bill_id}", changing=current) or []
        return sorted({e["Vote"]["VoteId"] for e in log
                       if isinstance(e.get("Vote"), dict) and e["Vote"].get("VoteId")})  # fmt: skip

    def _walk(self, starts: list[str], route: str) -> int:
        """Follow Next links from each start (statutes: title → chapter → section; rules),
        stopping at a node already walked. Returns how many nodes there are."""
        seen: set[str] = set()
        for at in starts:
            while at and at not in seen:
                seen.add(at)
                node = self.get(route.format(at))
                if not isinstance(node, dict):
                    break
                at = node.get("Next")
        return len(seen)

    def step_statutes(self) -> None:
        titles = [str(t["Statute"]) for t in self.get("Statutes/Title", changing=True) or []]
        logger.info("legislature: %d statute nodes", self._walk(titles, "Statutes/Statute/{}"))
        articles = [
            str(a["Statute"]) for a in self.get("Statutes/Constitution", changing=True) or []
        ]
        logger.info("legislature: %d constitution nodes",
                    self._walk(articles, "Statutes/Constitution/{}"))  # fmt: skip

    def step_rules(self) -> None:
        rules = [r["RuleNumber"] for r in self.get("Rules", changing=True) or []]
        logger.info("legislature: %d rule nodes", self._walk(rules, "Rules/{}"))

    def step_historical(self) -> None:
        for m in self.get("Historical/AllFlatMembers", changing=True) or []:
            self.get(f"Historical/Member/{m['MemberId']}")

    def step_staff(self) -> None:
        for u in self.get("Users", changing=True) or []:
            self.get(f"Users/{u['UserId']}", changing=True)

    def step_documents(self) -> None:
        """Every document type's listing (document records, not the files)."""
        for t in DOCUMENT_TYPES:
            self.get(f"Documents/DocumentType?Type={t}", changing=True)


def raw_root() -> Path:
    from django.conf import settings

    return Path(settings.MEDIA_ROOT) / "raw" / "legislature"
