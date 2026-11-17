"""The press association's newspaper directory: the parser against the table the directory's
widget returned (fixtures/table.json, 2026-11-16, checked by hand against the page), the
page's widget details against a cut of the real page, and a run into the database.

The live test isn't recorded: the widget's API needs the site's access token, which a
cassette would keep. Run it with `just test-network app/records/sources/newspaper_directory`.
"""

import json
from pathlib import Path

import pytest

from entities.models import Mention
from records.models import Record, RecordSet
from records.run import run_source
from records.sources.newspaper_directory import parse, source

FIXTURES = Path(__file__).parent / "fixtures"
TABLE = json.loads((FIXTURES / "table.json").read_text())
PAGE = (FIXTURES / "directory-page.html").read_text()


def papers() -> dict:
    return {p.publication_name: p for p in parse.newspapers(TABLE)}


def test_every_paper_is_read():
    found = papers()
    assert len(found) == 108
    mail = found["South Dakota Mail"]
    assert (mail.city, mail.county, mail.zip, mail.phone, mail.email) == (
        "Plankinton", "Aurora", "57368", "605-942-7770", "sdmail@siouxvalley.net")  # fmt: skip
    assert mail.website is None  # the directory gives the paper's name, no address
    assert mail.legal_newspaper is True and mail.publishers == ["John Paul Studeny"]
    huron = found["Huron Plainsman"]
    assert huron.website == "http://www.plainsman.com"
    assert huron.publishers == ["Champion Media SD, LLC", "Shaun Sarvis"]
    assert found["The Collegian (SDSU)"].legal_newspaper is False
    times = found["Dakota Dunes North Sioux City Times"]
    assert times.cities == ["North Sioux City", "Dakota Dunes"] and times.zip == "57049"
    assert times.publishers == ["Legacy Publishing", "Shannon Jost"]  # "|" too
    assert sum(p.legal_newspaper for p in found.values()) == 101


def test_where_the_table_comes_from():
    found = parse.widget(PAGE)
    assert found["table"] == "ad34f223-54da-4b2c-8765-d085eecd65c6"
    assert found["app"] == "55ab4470-f20a-4209-b74f-d0fc9db42eea"
    assert found["host"] == "ema1hh-bde62badf4e3ed67-bieliaievd.wix-host.com"


def test_a_changed_layout_fails_loudly():
    with pytest.raises(parse.LayoutChanged, match="table widget"):
        parse.widget("<html>no table here</html>")
    changed = json.loads(json.dumps(TABLE))
    changed["snapshot"]["columns"][1]["id"] = "county-name"
    with pytest.raises(parse.LayoutChanged, match="columns"):
        parse.newspapers(changed)


class FakeFetcher:
    """The page, the site's access tokens and the table, as the site answers."""

    def __init__(self):
        self.asked = []

    def request(self, method, url, **kwargs):
        self.asked.append((url, kwargs.get("headers", {})))

        class Resp:
            text = ""

            def json(self):
                return body

        body = None
        if url == source.PAGE:
            Resp.text = PAGE
        elif url == source.TOKENS:
            body = {"apps": {"55ab4470-f20a-4209-b74f-d0fc9db42eea": {"instance": "TOKEN"}}}
        else:
            body = TABLE
        return Resp()


def test_a_run(db, sd_counties, sd_places):
    RecordSet.objects.create(slug="newspaper-directory", name="Newspapers",
                             dataset="newspapers")  # fmt: skip
    fetcher = FakeFetcher()
    assert run_source("newspaper-directory", fetcher=fetcher).added == 108
    table_url, headers = fetcher.asked[-1]
    assert table_url.endswith("/api/table?id=ad34f223-54da-4b2c-8765-d085eecd65c6")
    assert headers["authorization"] == "TOKEN"
    times = Record.objects.get(key="Dakota Dunes North Sioux City Times "
                                   "(North Sioux City/Dakota Dunes)")  # fmt: skip
    assert sorted(times.places.values_list("name", flat=True)) == [
        "Dakota Dunes CDP", "North Sioux City city", "South Dakota", "Union County"]  # fmt: skip
    huron = Mention.objects.filter(record_key="newspaper-directory:Huron Plainsman (Huron)")
    found = {(m.name, m.role, m.schema) for m in huron}
    assert ("Shaun Sarvis", "newspaper publisher", "Person") in found
    assert ("Huron Plainsman", "newspaper", "Organization") in found  # never a person
    assert ("Champion Media SD, LLC", "newspaper publisher", "Company") in found
    # a paper's name with "&" is one paper; a one-word publisher is an organization
    tyndall = Mention.objects.filter(record_key__contains="Tyndall Tribune & Register")
    assert [(m.name, m.schema) for m in tyndall if m.field == "publication_name"] == [
        ("Tyndall Tribune & Register", "Organization")]  # fmt: skip
    gannett = Mention.objects.filter(dataset="newspapers", name="Gannett")
    assert gannett and {m.schema for m in gannett} == {"Organization"}
    assert {m[0] for m in found} == {"Huron Plainsman", "Champion Media SD, LLC", "Shaun Sarvis"}


@pytest.mark.network
def test_the_live_directory(tmp_path):
    import sdfetch

    items = source.items(sdfetch.Fetcher(tmp_path, delay=0))
    assert len(items) >= source.MIN_PAPERS
    assert len({i.key for i in items}) == len(items)
