"""The legislature's record sets, built from real stored API responses (trimmed to a few
records): the 2026 session, HB 1001 and one of its versions, a roll call, the full House,
Sen. Buhl O'Donnell in the Historical Listing, a staff member, a session law, a statute
chapter and its sections, and a rules chapter and rule."""

import shutil
from pathlib import Path

import pytest

from records.sources._legislature import sets

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def raw(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    shutil.copytree(FIXTURES / "api", tmp_path / "raw" / "legislature" / "api")
    return tmp_path


def one(found, key):
    (item,) = [i for i in found if i.key == key]
    return item


def test_bills_and_versions(raw):
    bill = one(sets.bills(), "26699")
    assert bill.data["BillNumber"] == 1001 and len(bill.data["ActionLog"]) == 13
    assert bill.data["Audio"][0]["Url"].endswith(".mp3")  # the index, not the file
    assert bill.source_url == "https://sdlegislature.gov/Session/Bill/26699"
    versions = sets.bill_versions()
    assert versions.checks["text_not_yet_stored"] == 2  # one of its three is in the fixtures
    (v,) = versions
    assert v.data["BillVersion"] == "Introduced" and v.data["BillId"] == 26699
    assert "<" in v.data["DocumentHtml"] and "prescribed burning" in v.data["text"]


def test_historical_legislators_and_their_mentions(raw, db):
    member = one(sets.historical_legislators(), "3830")
    assert member.data["LastName"] == "Buhl O'Donnell"
    assert member.data["MemberServices"][0]["StartYear"] == 2011
    assert "?" not in member.data["Pictures"][0]["Url"]  # no expiring signature
    assert member.data["Pictures"][0]["ParsedText"].startswith("Angie Buhl")
    from records.models import Record, RecordSet

    rs = RecordSet.objects.create(slug="legislature-historical-legislators", name="H",
                                  dataset="legislature")  # fmt: skip
    rec = Record(recordset=rs, key="3830", data=member.data)
    (m,) = sets.historical_mentions(rec)
    assert m.name == "Buhl O'Donnell, Angela Marie" and m.gold_entity_id == "sd-legislator:3830"


def test_the_rest(raw):
    assert one(sets.sessions(), "71").data["Documents"]
    vote = one(sets.roll_calls(), "82647")
    assert vote.data["Yeas"] == 6 and len(vote.data["RollCalls"]) == 7
    house = one(sets.committees(), "1266")
    assert house.data["FullBody"] and house.source_url.endswith("/Session/Committee/1266/Detail")
    law = one(sets.session_laws(), "8265")
    assert "citizen of the United States" in law.data["text"]
    person = one(sets.staff(), "44")
    assert person.source_url == "https://sdlegislature.gov/Staff/Profile/123"  # by UserId


def test_a_chapter_leaves_its_text_to_its_sections(raw):
    statutes = {i.key: i.data for i in sets.statutes()}
    assert set(statutes) == {"21-11", "21-11-1", "21-11-2"}
    assert statutes["21-11"]["text_in_children"] and "Html" not in statutes["21-11"]
    assert "Html" in statutes["21-11-1"] and statutes["21-11-1"]["text"]
    rules = {i.key: i.data for i in sets.rules()}
    assert rules["01:15:01"].get("text_in_children") and rules["01:15:01:01"]["text"]


def test_imported_records_are_keyed_by_their_page():
    def rec(key):
        return type("R", (), {"key": key})()

    assert sets.legacy_id(rec("https://sdlegislature.gov/Session/Bill/26699")) == "26699"
    assert sets.legacy_id(rec("https://sdlegislature.gov/Session/Committee/1266/Detail")) == "1266"
    from_legacy = sets.legacy_fields({"session_id": ("SessionId", int),
                                      "is_current_session": ("CurrentSession", bool),
                                      "notes": ("Notes", str)})  # fmt: skip
    assert from_legacy({"session_id": "71", "is_current_session": "True", "notes": "None"}) == {
        "SessionId": 71, "CurrentSession": True, "Notes": None}  # fmt: skip
