"""Legislators' biographies: real stored pages (the API's member responses, trimmed to one
picture each, signatures removed; the page images; our OCR of them), checked by hand
against the images.

- Joanne Lockner, 1993 Blue Book: the run-on layout; the two readings disagree on the
  spouse ("Frank 0" / "Frank O. Lockner").
- James Putnam, 2011: the address on its own lines, unlabeled.
- Bob Ewing, 2013: every part labeled.
- Lee Schoenbeck, 2019: the legislature's OCR repeats each line; the image's crop takes in
  another bio's committees, so the readings disagree there.
- Robert Dollard, Biographical Directory: prose, kept as text.
"""

import json
import shutil
from pathlib import Path

import pytest

from records.sources.legislature_blue_book_bios import images, parse, source

FIXTURES = Path(__file__).parent / "fixtures"
MEMBERS = FIXTURES / "raw" / "legislature" / "api" / "Historical" / "Member"


def picture(mid: int) -> dict:
    return json.loads((MEMBERS / f"{mid}.json").read_text())["Pictures"][0]


def test_repeated_ocr_lines_collapse_to_their_most_common_version():
    lines = ["Address: 1200 Mickelson Dr., Watertown"] * 3 + [
        "Address: 1200 rvlickelson Dr., Watertown", "Spouse: Donna."]  # fmt: skip
    assert parse.collapse_repeats(lines) == ["Address: 1200 Mickelson Dr., Watertown",
                                             "Spouse: Donna."]  # fmt: skip
    assert parse.join_lines(["Bon Homme, Douglas, Hutchin-", "son and Turner"]) == (
        "Bon Homme, Douglas, Hutchinson and Turner")  # fmt: skip


def test_run_on_parts_end_at_the_sentence():
    assert parse.first_sentence("Doris Marie Strom. Congregational. BS, 1950") == (
        "Doris Marie Strom", "Congregational. BS, 1950")  # fmt: skip
    assert parse.first_sentence("Leah, Paul R., Mary L. James") == (
        "Leah, Paul R., Mary L. James",
        "",
    )


def test_the_layouts():
    lockner = parse.read(picture(2976)["ParsedText"])  # 1993
    assert (lockner["party"], lockner["district"]) == ("Democrat", "22")
    assert (
        lockner["counties"] == "Aurora, Buffalo, Hand, Jerauld, Sanborn and part of Spink counties"
    )
    assert lockner["birth"] == "May 19, 1937, St. Lawrence, SD"
    putnam = parse.read(picture(3052)["ParsedText"])  # 2011
    assert putnam["name"] == "J. E. \u201cJim\u201d Putnam"  # curly quotes, as printed
    assert putnam["address"] == "PO Box 490 Armour, SD 57313"
    schoenbeck = parse.read(picture(1474)["ParsedText"])  # 2019, lines repeated
    assert schoenbeck["email"] == "Lee.Schoenbeck@sdlegislature.gov"
    assert schoenbeck["address"] == "PO Box 1325, Watertown, SD 57201"


@pytest.fixture
def raw(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    shutil.copytree(FIXTURES / "raw", tmp_path / "raw")
    return tmp_path


def test_a_run(raw):
    found = {i.key: i for i in source.items()}
    assert found and len(found) == 5
    ewing = found["5067"].data
    assert ewing["unconfirmed"] == [] and ewing["party_agrees"] is True
    assert (ewing["birth"], ewing["spouse"]) == ("November 12th, 1954", "Sherry")
    assert ewing["committees"].startswith("Agriculture and Natural Resources")
    assert found["5067"].places == ["0400000US46", "0500000US46081"]  # Lawrence County
    assert (
        found["5067"].source_url == "https://sdlegislature.gov/Historical/Legislator/Profile/3849"
    )
    lockner = found["3557"].data
    assert lockner["spouse"] is None and lockner["readings"]["spouse"] == {
        "legislature": "Frank 0", "ours": "Frank O. Lockner"}  # fmt: skip
    assert len(found["3557"].places) == 7  # the state and six counties
    schoenbeck = found["4245"].data
    assert "committees" in schoenbeck["unconfirmed"] and schoenbeck["committees"] is None
    dollard = found["1547"].data
    assert dollard["kind"] == "Biographical Directory" and "ROBERT DOLLARD" in dollard["text"]
    assert "party" not in dollard  # prose isn't read into parts


def test_mentions(raw, db):
    from records.models import Record, RecordSet

    rs = RecordSet.objects.create(
        slug="legislature-blue-book-bios", name="B", dataset="legislature"
    )
    found = {i.key: i for i in source.items()}
    ewing = source.mentions(Record(recordset=rs, key="5067", data=found["5067"].data))
    assert [(m.name, m.gold_entity_id) for m in ewing] == [("Bob Ewing", "sd-legislator:3849")]
    data = {**found["3557"].data, "spouse": "Frank O. Lockner"}  # as if both readings agreed
    lockner = source.mentions(Record(recordset=rs, key="3557", data=data))
    assert [m.name for m in lockner] == ["Joanne Lockner", "Frank O. Lockner"]


class FakeFetcher:
    """The member's listing (with fresh links) and its images."""

    def __init__(self, store):
        self.store, self.asked = store, []

    def fetch(self, url, name, refresh=False, **kwargs):
        self.asked.append(url)
        body = (MEMBERS / "3849.json").read_bytes() if "/api/" in url else b"RIFF....WEBP"
        self.store.save(name, body, url)
        return body


def test_images_are_fetched_once(raw):
    from sdfetch import Store

    store = Store(raw / "raw" / "legislature")
    image = store.path("bio-images/5067.webp")
    image.unlink()
    fetcher = FakeFetcher(store)
    counts = images.fetch_images(fetcher, [3849])
    assert counts["images"] == 1 and counts["members_refreshed"] == 1 and image.exists()
    assert images.fetch_images(fetcher, [3849])["members_refreshed"] == 0  # stored: no refetch
