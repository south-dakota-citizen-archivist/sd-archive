"""Athlete agent registrations: the parser against rows cut from the real page (checked by
hand), the source against a recording of the site, and a run into the database.
Re-record when the site changes: just record app/records/sources/athletic_agents"""

from pathlib import Path

import pytest

import sdfetch
from entities.models import Mention
from records.models import Record, RecordSet
from records.run import run_source
from records.sources.athletic_agents import parse, source

FIXTURE = (Path(__file__).parent / "fixtures" / "athletic-agent-registration.html").read_text()
FORMS = "https://sdsos.gov/general-information/miscellaneous-forms/Athletic_Agent_Reg_Forms/"


def test_parse_reads_every_row():
    rows = {r.registration.agent_name: r for r in parse.rows(FIXTURE, source.URL)}
    assert list(rows) == ["Banks, Chris", "Roby, Christopher", "Malinowski, Travis",
                          "Lutey, Dylan", "Perez, John", "Boras, Scott D."]  # fmt: skip
    banks = rows["Banks, Chris"]
    reg = banks.registration
    assert (str(reg.effective_date), str(reg.expiration_date)) == ("2024-10-17", "2026-10-16")
    assert reg.effective_date_raw == "10/17/2024"  # a trailing space on the site
    assert banks.form_url == FORMS + "2024/Banks.PDF"
    assert rows["Perez, John"].form_url == FORMS + "2025/Perez(Renewal).pdf"
    # the site links two agents to the same form; each keeps the link as published
    assert rows["Malinowski, Travis"].form_url == rows["Lutey, Dylan"].form_url


def test_a_changed_layout_fails_loudly():
    with pytest.raises(parse.LayoutChanged, match="columns"):
        parse.rows(FIXTURE.replace("Expiration Date", "Expires"), source.URL)
    with pytest.raises(parse.LayoutChanged, match="cells"):
        parse.rows(FIXTURE.replace("<td>10/16/2026</td>", "", 1), source.URL)


class FixtureFetcher:
    def request(self, method, url, **kwargs):
        class Resp:
            content = FIXTURE.encode()

        return Resp()


def test_run_stores_records_documents_and_mentions(db, state):
    RecordSet.objects.create(slug="athletic-agents", name="Athlete agents",
                             dataset="athletic-agents", ingest="documents")  # fmt: skip
    assert run_source("athletic-agents", fetcher=FixtureFetcher()).added == 6
    banks = Record.objects.get(key="2024-10-17 Banks, Chris")
    assert banks.source_url == FORMS + "2024/Banks.PDF"
    assert list(banks.places.all()) == [state]
    assert banks.documents.get().url == FORMS + "2024/Banks.PDF"
    mention = Mention.objects.get(record_key="athletic-agents:2024-10-17 Banks, Chris")
    assert (mention.name, mention.role, mention.properties["lastName"]) == (
        "Banks, Chris",
        "athlete agent",
        ["Banks"],
    )


def test_the_form_has_a_schema():
    doc = source.SOURCE.schema_for("registration-form")
    assert doc is not None and "sports" in doc.model_fields


@pytest.mark.vcr
def test_the_live_page(tmp_path):
    items = source.items(sdfetch.Fetcher(tmp_path, delay=0))
    assert len(items) >= 20
    assert len({i.key for i in items}) == len(items)
    assert all(i.documents and i.data["effective_date"] for i in items)
