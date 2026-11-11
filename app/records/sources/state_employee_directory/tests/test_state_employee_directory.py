"""The state phone directory: the parser against real API responses (checked by hand), a run
against those responses, and the live site (a network test: uv run pytest -m network)."""

import json
from pathlib import Path

import pytest

import sdfetch
from entities.models import Mention
from records.models import Record, RecordSet
from records.run import run_source
from records.sources.state_employee_directory import parse, source

FIXTURES = Path(__file__).parent / "fixtures"
AUDITOR, TREASURER, HOUSING = ("56a9ca6e1ba5b8101c4fa97ae54bcb0a",
                               "88684eaa1ba5b8101c4fa97ae54bcb12",
                               "0de54b161b6ec910b9c24262f54bcb3e")  # fmt: skip


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def test_agencies():
    agencies = dict(parse.agencies(fixture("agency-facet")))
    assert len(agencies) == 32
    assert agencies[AUDITOR] == "State Auditor"
    with pytest.raises(parse.LayoutChanged):
        parse.agencies({"result": {}})


def test_entries():
    found = parse.entries(fixture("state-auditor-0")) + parse.entries(fixture("state-auditor-1"))
    assert len(found) == fixture("state-auditor-0")["result"]["data"]["row_count"] == 16
    assert all(e["full_name"] and e["phone"].startswith("+1 (605)") for e in found)
    assert all(e["phone_e164"].startswith("+1605") for e in found)
    assert parse.entries(fixture("housing-0")) == []
    with pytest.raises(parse.LayoutChanged):
        parse.entries({"result": {"data": {}}})


def test_people_and_offices():
    assert parse.person_parts("Briana Aadland") == ("Briana", "Aadland")
    for office in ("DOC-SDSP COMS UNIT A", "DSS HSC Laboratory", "DOE ArchivistEmail"):
        assert parse.person_parts(office) == (None, None)
    assert parse.person_parts("GERALYN FRIEDERICH") == ("GERALYN", "FRIEDERICH")


class FixtureFetcher:
    """The site cut to three agencies (one empty) and the entries in no agency; the overall
    count is theirs added up."""

    def __init__(self):
        self.parts = {AUDITOR: "state-auditor", TREASURER: "state-treasurer", HOUSING: "housing"}

    def request(self, method, url, **kwargs):
        class Resp:
            text = "<script>var g_ck = 'fixture-token';</script>"

        return Resp()

    def fetch(self, url, name, *, data=None, headers=None, **kwargs):
        assert headers["X-UserToken"] == "fixture-token" and headers["x-portal"] == source.PORTAL
        if data is None:
            facet = fixture("agency-facet")
            facet["result"]["facets"][0]["choices"] = [
                c for c in facet["result"]["facets"][0]["choices"] if c["value"] in self.parts
            ]
            return json.dumps(facet).encode()
        body = json.loads(data)
        query, page = body["filterObj"]["filter"], body["page"]
        if "NOT IN" in query:
            return (FIXTURES / f"remainder-{page}.json").read_bytes()
        for aid, part in self.parts.items():
            if f"agencyIN{aid}" in query:
                return (FIXTURES / f"{part}-{page}.json").read_bytes()
        total = fixture("remainder-0")
        total["result"]["data"]["row_count"] = 16 + 10 + 0 + 34
        return json.dumps(total).encode()


def test_run(db, state):
    RecordSet.objects.create(slug="state-employee-directory", name="Phone directory",
                             dataset="state-employee-directory")  # fmt: skip
    run = run_source("state-employee-directory", fetcher=FixtureFetcher())
    assert run.added == run.fetched_count == 16 + 10 + 34
    auditor = [r for r in Record.objects.all() if r.data["agencies"] == ["State Auditor"]]
    assert len(auditor) == 16
    no_agency = Record.objects.filter(data__agencies=[]).count()
    assert no_agency == 34
    person = next(r for r in auditor if r.data["given_name"])
    m = Mention.objects.get(record_key=f"state-employee-directory:{person.key}")
    assert m.context["agencies"] == ["State Auditor"] and list(person.places.all()) == [state]


def test_a_short_read_fails():
    class Short(FixtureFetcher):
        def fetch(self, url, name, *, data=None, **kwargs):
            got = super().fetch(url, name, data=data, **kwargs)
            if data and "agencyIN" + AUDITOR in json.loads(data)["filterObj"]["filter"]:
                return (FIXTURES / "state-auditor-0.json").read_bytes()  # page 2 lost
            return got

    with pytest.raises(parse.LayoutChanged, match="read 20 entries of 16"):
        source.items(Short())


@pytest.mark.network  # the session page is ~1 MB and carries a session token: not recorded
def test_the_live_site(tmp_path):
    session = source.Session(sdfetch.Fetcher(tmp_path, delay=0), month="2026-11")
    agencies = dict(parse.agencies(session.page_data()))
    assert len(agencies) >= 30 and "State Auditor" in agencies.values()
    first = session.results(f"{source.BASE}^agencyIN{AUDITOR}", 0)
    assert parse.entries(first)
