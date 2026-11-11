"""The legislature crawl: names, reuse, refresh, missing pages, and the walks it makes."""

import json
import os
import time

import pytest
import requests

import sdfetch
from records.sources._legislature import crawl


class FakeAPI:
    """Answers paths from a dict (a missing path is a 404), counting what it was asked."""

    def __init__(self, store, answers):
        self.store, self.answers, self.asked = store, answers, []

    def fetch(self, url, name, *, refresh=False, **kwargs):
        path = url.removeprefix(crawl.API)
        self.asked.append(path)
        if path not in self.answers:
            resp = requests.Response()
            resp.status_code = 404
            raise requests.HTTPError("404", response=resp)
        body = json.dumps(self.answers[path]).encode()
        self.store.save(name, body, url)
        return body


def test_store_names():
    assert crawl.store_name("Bills/26699") == "api/Bills/26699.json"
    assert crawl.store_name("Rules/01:01") == "api/Rules/01_01.json"
    assert crawl.store_name("SessionCommittees/Documents/1266?Type=2&Type=4") == (
        "api/SessionCommittees/Documents/1266__Type_2_Type_4.json"
    )


def test_get_reuses_refreshes_and_remembers_404s(tmp_path):
    api = FakeAPI(sdfetch.Store(tmp_path), {"Sessions": [{"SessionId": 71}], "Bills/1": {"a": 1}})
    c = crawl.Crawl(api)
    assert c.get("Bills/1") == {"a": 1} and c.get("Bills/1") == {"a": 1}
    assert api.asked == ["Bills/1"], "a stored response is reused"
    assert c.get("Bills/2") is None and c.get("Bills/2") is None
    assert api.asked.count("Bills/2") == 1, "a 404 isn't asked again"
    assert crawl.Crawl(api).get("Bills/2") is None and api.asked.count("Bills/2") == 1

    c.get("Sessions", changing=True)
    stale = tmp_path / "api/Sessions.json"
    os.utime(stale, (time.time() - crawl.REFRESH - 60,) * 2)
    crawl.Crawl(api).get("Sessions", changing=True)
    assert api.asked.count("Sessions") == 2, "a changing response is refetched when stale"


def test_sessions_walk_bills_votes_members_and_committees(tmp_path):
    answers = {
        "Sessions": [{"SessionId": 70}, {"SessionId": 71, "CurrentSession": True}],
        "Bills/Session/71": [{"BillId": 5}],
        "Bills/ActionLog/5": [{"Vote": {"VoteId": 9}}, {"Vote": None}, {"Vote": {"VoteId": 9}}],
        "Votes/9": {"VoteId": 9},
        "SessionMembers/Session/71": [{"SessionMemberId": 3}],
        "SessionCommittees/Session/71": [{"SessionCommitteeId": 4}],
        "InterimCommittees/Session/71": [{"InterimYearCommitteeId": 6}],
        "SessionLaws/71": [{"SessionLawId": 8}],
    }
    api = FakeAPI(sdfetch.Store(tmp_path), answers)
    crawl.Crawl(api).run(["sessions"], sessions=[71])
    for path in ("Bills/5", "Bills/Audio/5", "Votes/9", "SessionMembers/Detail/3",
                 "SessionCommittees/CommitteeAgendas/4", "InterimCommittees/Detail/6",
                 "SessionLaws/SessionLaw/8", "Keywords?SessionId=71"):  # fmt: skip
        assert path in api.asked, path
    assert api.asked.count("Votes/9") == 1
    assert not any(p.endswith("/70") for p in api.asked), "only the session asked for"


def test_statutes_follow_next_links_from_every_title(tmp_path):
    answers = {
        "Statutes/Title": [{"Statute": "1"}, {"Statute": "2"}],
        "Statutes/Constitution": [],
        "Statutes/Statute/1": {"Next": "1-1"},
        "Statutes/Statute/1-1": {"Next": "1-1-1"},
        "Statutes/Statute/1-1-1": {"Next": None},  # a gap: title 2 isn't linked from here
        "Statutes/Statute/2": {"Next": "2-1"},
        "Statutes/Statute/2-1": {"Next": "1"},  # back to the start: stop
    }
    api = FakeAPI(sdfetch.Store(tmp_path), answers)
    crawl.Crawl(api).step_statutes()
    walked = [p for p in api.asked if p.startswith("Statutes/Statute/")]
    assert walked == ["Statutes/Statute/1", "Statutes/Statute/1-1", "Statutes/Statute/1-1-1",
                      "Statutes/Statute/2", "Statutes/Statute/2-1"]  # fmt: skip


def test_other_errors_stop_the_crawl(tmp_path):
    class Down(FakeAPI):
        def fetch(self, url, name, **kwargs):
            resp = requests.Response()
            resp.status_code = 503
            raise requests.HTTPError("503", response=resp)

    with pytest.raises(requests.HTTPError):
        crawl.Crawl(Down(sdfetch.Store(tmp_path), {})).get("Sessions")
