"""Supreme Court justices: the two list pages of the real PDF (checked by hand), and a run."""

from pathlib import Path

from entities.models import Mention
from records.models import Record, RecordSet
from records.run import run_source
from records.sources.supreme_court_justices import parse

PDF = (Path(__file__).parent / "fixtures" / "members-pages.pdf").read_bytes()


def test_the_lists():
    members, chiefs, pages = parse.lists(PDF)
    assert len(members) == 54 and len(chiefs) == 8
    assert pages == {"members": 1, "chief_justices": 2}
    assert members[0] == {"name": "Dighton Corson", "marks": "*", "district": 1, "start": 1889,
                          "end": 1913,
                          "as_published": "*Dighton Corson, District 1 1889 – 1913"}  # fmt: skip
    mccoy = next(m for m in members if m["name"] == "James H. McCoy")
    assert mccoy["marks"] == "*+"
    assert next(m for m in members if m["name"] == "Oren P. (Phil) Coler")["start"] == 1974
    assert members[-1]["name"] == "Robert Gusinsky" and members[-1]["end"] is None
    assert chiefs[-1] == {
        "name": "Steven R. Jensen", "marks": "", "district": 4, "start": 2021, "end": None,
        "as_published": "Steven R. Jensen, District 4 2021 –",
    }  # fmt: skip


class FixtureFetcher:
    store = None

    def request(self, method, url, **kwargs):
        assert url == parse.URL
        return type("R", (), {"content": PDF})()


def test_run(db, state):
    RecordSet.objects.create(slug="supreme-court-justices", name="Justices",
                             dataset="supreme-court-justices")  # fmt: skip
    run = run_source("supreme-court-justices", fetcher=FixtureFetcher())
    assert run.added == 54 and run.checks["chief_justices_not_members"] == []
    gilbertson = Record.objects.get(key="david-gilbertson")
    assert gilbertson.data["chief_justice"] is True
    assert gilbertson.data["chief_justice_terms"] == [{"start": 2001, "end": 2021}]
    assert gilbertson.source_url.endswith("#page=1")
    corson = Record.objects.get(key="dighton-corson").data
    assert corson["presiding_judge"] is True and corson["chief_justice"] is False
    assert Record.objects.get(key="james-h-mccoy").data["joined_in_1909_expansion"] is True
    assert Record.objects.filter(data__sitting=True).count() == 5
    assert Mention.objects.get(record_key="supreme-court-justices:robert-gusinsky").role == (
        "supreme court justice"
    )
