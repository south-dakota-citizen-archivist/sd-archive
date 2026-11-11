"""Right of publicity registrations: the parser against rows cut from the real page (checked
by hand), the source against a recording of the site, and a run into the database.
Re-record when the site changes: just record app/records/sources/publicity_rights"""

from pathlib import Path

import pytest

import sdfetch
from entities.models import Mention
from records import fixes
from records.models import Record, RecordSet
from records.run import run_source
from records.sources.publicity_rights import parse, source

FIXTURE = (Path(__file__).parent / "fixtures" / "personality-right-publicity.html").read_text()
ASSETS = "https://sdsos.gov/general-information/assets/"
FOLDER = ASSETS + "Personalitys%20Right%20of%20Publicity/"


def rows():
    return {r.personality_name: r for r in parse.rows(FIXTURE, source.URL)}


def test_parse_reads_every_row():
    got = rows()
    assert list(got) == [
        "Russell Charles Means",
        "Cecil B. Demille",
        "Louis Armstrong",
        "Kirk Douglas",
        "Clyde Fessler Jr.",
        "HOFER (Family Surname/Personality Estate)",
        "John Coltrane",
    ]
    means = got["Russell Charles Means"]
    assert (means.claimant_name, str(means.date_filed)) == ("Pearl Daniel Means", "2015-07-21")
    assert means.form_url == ASSETS + "MeansRussellCharles20151020.pdf"
    # a comma in the wrong place
    assert (got["Cecil B. Demille"].date_filed_raw, str(got["Cecil B. Demille"].date_filed)) == (
        "December, 20 2017",
        "2017-12-20",
    )
    # spaces in the link
    assert got["Louis Armstrong"].form_url == (
        ASSETS + "Louis%20Armstrong%20_Personalitys_Right_of_Publicity.PDF"
    )
    assert got["Kirk Douglas"].corrections == []


def test_a_wrong_link_is_corrected_and_the_correction_kept():
    fessler = rows()["Clyde Fessler Jr."]
    assert fessler.form_url == FOLDER + "ClydeFesslerJr.PDF"
    [fix] = fessler.corrections
    assert (fix["published"], fix["corrected"]) == (FOLDER + "KirkDouglas.PDF",
                                                    FOLDER + "ClydeFesslerJr.PDF")  # fmt: skip
    assert "Kirk Douglas" in fix["reason"]


def test_a_fix_stops_once_the_source_is_corrected(caplog):
    values = {"personality_name": "Clyde Fessler Jr.", "form_url": FOLDER + "Fessler.pdf"}
    assert fixes.apply(values, parse.FIXES) == []
    assert values["form_url"] == FOLDER + "Fessler.pdf"
    assert "stale fix" in caplog.text


def test_a_changed_layout_fails_loudly():
    with pytest.raises(parse.LayoutChanged, match="columns"):
        parse.rows(FIXTURE.replace("Name of Claimant", "Claimant"), source.URL)


class FixtureFetcher:
    def request(self, method, url, **kwargs):
        class Resp:
            content = FIXTURE.encode()

        return Resp()


def test_run_stores_records_documents_and_mentions(db, state):
    RecordSet.objects.create(slug="publicity-rights", name="Right of publicity",
                             dataset="publicity-rights", ingest="documents")  # fmt: skip
    assert run_source("publicity-rights", fetcher=FixtureFetcher()).added == 7
    fessler = Record.objects.get(key="2021-12-20 Clyde Fessler Jr.")
    assert fessler.documents.get().url == FOLDER + "ClydeFesslerJr.PDF"
    assert fessler.source_url == FOLDER + "ClydeFesslerJr.PDF"
    douglas = Mention.objects.filter(record_key="publicity-rights:2021-12-20 Kirk Douglas")
    assert {m.name for m in douglas if m.role == "claimant"} == {
        "Anita Resenstein",
        "Fayez Sarofim",
        "Stuart Korshak",
        "James Greenbaum",
        "Arnold Rosenstein",
    }
    assert douglas.get(role="personality").name == "Kirk Douglas"


@pytest.mark.vcr
def test_the_live_page(tmp_path):
    items = source.items(sdfetch.Fetcher(tmp_path, delay=0))
    assert len(items) >= 32
    assert len({i.key for i in items}) == len(items)
    assert len({i.source_url for i in items}) == len(items), "each row links its own filing"
