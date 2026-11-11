"""Educator disciplinary actions: the real page (checked by hand) read and run, and the live
page against a recording.
Re-record when the site changes: just record app/records/sources/educator_disciplinary_actions"""

from pathlib import Path

import pytest

import sdfetch
from entities.models import Mention
from records.models import Record, RecordSet
from records.run import run_source
from records.sources._tables import LayoutChanged
from records.sources.educator_disciplinary_actions import source

PAGE = (Path(__file__).parent / "fixtures" / "EDA.html").read_bytes()


def test_rows():
    rows = source.rows(PAGE)
    assert len(rows) == 157
    assert rows[0] == {
        "name": "Gordon Hooks-Williams", "file_number": "DSE 2026-01",
        "action": "Application denied; Permanent revocation", "date_effective": "2026-07-07",
        "date_effective_raw": "07/07/2026", "date_reinstated": None,
        "date_reinstated_raw": "N/A",
        "decision_url": "https://doe.sd.gov/professionalpractices/documents/EDA/DSE-2026-01.pdf",
    }  # fmt: skip
    reinstated = [r for r in rows if r["date_reinstated"]]
    assert reinstated and all(r["date_reinstated"][:2] == "20" for r in reinstated)


def test_keys():
    rows = source.rows(PAGE)
    assert len({source.key(r) for r in rows}) == 157
    pliska = next(r for r in rows if r["name"] == "Michelle Pliska")
    assert source.key(pliska) == "2024-08-06 Michelle Pliska"  # a reprimand has no file number


def test_a_changed_layout_fails_loudly():
    with pytest.raises(LayoutChanged):
        source.rows(PAGE.replace(b"File Number", b"Case Number"))


class FixtureFetcher:
    def request(self, method, url, **kwargs):
        class Resp:
            content = PAGE

        return Resp()


def test_first_run_makes_the_record_set(db, state):
    run = run_source("educator-disciplinary-actions", fetcher=FixtureFetcher())
    assert run.added == 157
    rs = RecordSet.objects.get(slug="educator-disciplinary-actions")
    assert (rs.dataset, rs.publisher.name, rs.ingest) == (
        "educator-disciplinary-actions",
        "South Dakota Department of Education",
        "documents",
    )
    first = Record.objects.get(key="DSE 2026-01")
    assert first.documents.get().url.endswith("/DSE-2026-01.pdf")
    m = Mention.objects.get(record_key="educator-disciplinary-actions:DSE 2026-01")
    assert (m.name, m.role, m.schema) == ("Gordon Hooks-Williams", "educator", "Person")


@pytest.mark.vcr
def test_the_live_page(tmp_path):
    rows = source.rows(sdfetch.Fetcher(tmp_path, delay=0).request("GET", source.URL).content)
    assert len(rows) >= 157
