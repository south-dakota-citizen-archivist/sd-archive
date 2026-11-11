"""Thrillshare documents: the Spearfish School District's real API answers for the Documents
root, the board minutes folder and one year's minutes (checked by hand), and a run."""

import json
from pathlib import Path

import pytest

from records.models import Document, Record, RecordSet
from records.run import run_source
from records.sources import _governments
from records.sources import _thrillshare as T

FIXTURES = Path(__file__).parent / "fixtures"
ANSWERS = {None: "thrill-root.json", 25745608: None, 25745856: "thrill-minutes.json",
           28249250: "thrill-leaf.json"}  # fmt: skip
EMPTY = {"items": [], "documents": [], "meta": {"folders": [], "links": {}}}


class FixtureFetcher:
    """The root lists eight folders; only Board of Education → Board Meeting Minutes →
    2026–2027 has anything in this stand-in."""

    store = None

    def request(self, method, url, params=None, **kwargs):
        assert url == T.API.format(site="547213") and params["page_no"] == 1
        folder = params.get("folder_id")
        name = ANSWERS.get(folder)
        if folder == 25745608:  # Board of Education: one subfolder, the minutes
            body = {**EMPTY, "meta": {"folders": [{"id": 25745856,
                                                   "folder_name": "Board Meeting Minutes"}],
                                      "links": {}}}  # fmt: skip
        elif name and folder != 25745856:
            body = json.loads((FIXTURES / name).read_text())
        elif folder == 25745856:  # its year folders: only 2026–2027 answers with files
            body = json.loads((FIXTURES / name).read_text())
            body["meta"]["folders"] = [f for f in body["meta"]["folders"] if f["id"] == 28249250]
        else:
            body = EMPTY
        return type("R", (), {"json": lambda self: body})()


def test_keys_and_dates():
    assert T.file_key("https://files-backend.assets.thrillshare.com/documents/asset/"
                      "uploaded_file/5977/Ssd/2e2a3924-3c8c-48a7-8029-a9cbadfe9eb0/x.pdf") == (
        "2e2a3924-3c8c-48a7-8029-a9cbadfe9eb0")  # fmt: skip
    token = "i1izrmfi9zp5ea9n22zbs05ftybj"
    url = f"https://files-backend.assets.thrillshare.com/./{token}?disposition=inline"
    assert T.file_key(url) == token
    assert T.file_key("https://docs.google.com/forms/d/e/x/viewform") is None
    assert T.date_named("a. July 14, 2025 School Board Agenda") == "2025-07-14"
    assert T.date_named("October 14,, 2025 minutes") == "2025-10-14"


def test_the_tree():
    found = T.files(FixtureFetcher(), "547213")
    assert len(found) == 8
    assert found[0]["folder_path"] == ["Board of Education", "Board Meeting Minutes",
                                       "2026 - 2027 School Board Minutes"]  # fmt: skip
    assert found[0]["name"] == "November 9, 2026 School Board Minutes"


@pytest.fixture
def district_only(monkeypatch):
    only = [g for g in _governments.GOVERNMENTS if "School District" in g["name"]]
    monkeypatch.setattr(_governments, "GOVERNMENTS", only)


def test_run(db, sd_all_places, district_only):
    RecordSet.objects.create(slug="thrillshare-documents", name="Documents",
                             dataset="school-district-documents")  # fmt: skip
    run = run_source("thrillshare-documents", fetcher=FixtureFetcher())
    assert run.added == 8
    minutes = Record.objects.get(key="547213:2e2a3924-3c8c-48a7-8029-a9cbadfe9eb0")
    assert minutes.data["date"] == "2026-11-09"
    assert minutes.data["folder"].startswith("Board of Education / Board Meeting Minutes")
    assert Document.objects.filter(record=minutes).count() == 1
    assert minutes.places.filter(geoid="4666930").exists()
