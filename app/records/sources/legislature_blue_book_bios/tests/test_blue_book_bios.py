"""Legislators' biographies: real stored pages (the API's member responses, trimmed to one
picture each, signatures removed; the page images; our OCR of them), checked by hand
against the images.

- Joanne Lockner, 1993 Blue Book: the run-on layout; the first two readings disagree on the
  spouse ("Frank 0" / "Frank O. Lockner") and the third (Apple Vision) settles it.
- James Putnam, 2011: the address on its own lines, unlabeled.
- Bob Ewing, 2013: every part labeled.
- Lee Schoenbeck, 2019: the legislature's OCR repeats each line; the image's crop takes in
  the end of the bio above (its committees), which is cut away before comparing.
- Robert Dollard, Biographical Directory: prose, kept as text.
"""

import json
import shutil
from pathlib import Path

import pytest

from records.sources.legislature_blue_book_bios import decisions, images, parse, source

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
    lockner = found["3557"].data  # "Frank 0" / "Frank O. Lockner" / Vision's "Frank O. Lockner"
    assert lockner["spouse"] == "Frank O. Lockner" and lockner["two_of_three"] == ["spouse"]
    assert len(found["3557"].places) == 7  # the state and six counties
    schoenbeck = found["4245"].data
    # our reading's image takes in the bio above's committees: cut to his own bio, they agree
    assert schoenbeck["committees"] == "Commerce and Energy and Judiciary"
    assert schoenbeck["two_of_three"] == ["experience"] and schoenbeck["unconfirmed"] == []
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


def test_a_reading_is_cut_to_the_legislators_own_bio():
    text = ("Judiciary (Chair), Taxation. Lee Schoenbeck (R) District 5 Committees: Judiciary. "
            "Tina Mulally (R) District 35 Email: y")  # fmt: skip
    assert parse.own_bio(text, "Schoenbeck") == (
        "Lee Schoenbeck (R) District 5 Committees: Judiciary.")  # fmt: skip
    assert parse.own_bio("no header", "Smith") == "no header"


def test_readings_agree_only_with_the_same_digits():
    assert source.agree("4605 S. Graceland Court", "4605 S. Graceiand Court")  # a letter slip
    assert not source.agree("13084 385th Avenue", "13034 385th Avenue")  # never a digit
    assert not source.agree("Frank 0", "Frank O. Lockner")


def ewing_misread(raw) -> tuple[dict, dict]:
    """Ewing's page with the legislature's OCR misreading the spouse, so the two readings
    disagree (no third reading of this page)."""
    path = raw / "raw" / "legislature" / "api" / "Historical" / "Member" / "3849.json"
    member = json.loads(path.read_text())
    pic = member["Pictures"][0]
    pic["ParsedText"] = pic["ParsedText"].replace("Spouse: Sherry", "Spouse: Sberry")
    path.write_text(json.dumps(member))
    return member, pic


def test_a_part_settled_by_hand(raw, tmp_path):
    from sdfetch import Store

    member, pic = ewing_misread(raw)
    store = Store(raw / "raw" / "legislature")
    data = source.page_item(member, pic, store, decided={}).data
    assert data["unconfirmed"] == ["spouse"] and data["spouse"] is None
    assert data["readings"]["spouse"] == {"legislature": "Sberry", "ours": "Sherry"}

    path = tmp_path / "fixes.toml"
    decisions.save({"image": "5067", "part": "spouse", "value": 'Sherry "Sher"',
                    "from": "typed", "reason": "Checked."}, path)  # fmt: skip
    decided = decisions.load(path)
    assert decided[("5067", "spouse")]["value"] == 'Sherry "Sher"'  # quotes survive the file
    data = source.page_item(member, pic, store, decided=decided).data
    assert data["spouse"] == 'Sherry "Sher"' and data["unconfirmed"] == []
    assert data["by_hand"]["spouse"]["from"] == "typed"
    assert data["readings"]["spouse"]["legislature"] == "Sberry"  # the readings are kept


def test_the_review_page(raw, tmp_path, db, client, monkeypatch):
    from records.models import Record, RecordSet

    monkeypatch.setattr(decisions, "PATH", tmp_path / "fixes.toml")
    monkeypatch.setattr("records.ingest._sync_places", lambda *a: None)
    member, pic = ewing_misread(raw)
    rs = RecordSet.objects.create(slug="legislature-blue-book-bios", name="B",
                                  dataset="legislature")  # fmt: skip
    from sdfetch import Store

    item = source.page_item(member, pic, Store(raw / "raw" / "legislature"))
    from django.utils import timezone

    record = Record.objects.create(recordset=rs, key=item.key, data=item.data,
                                   content_hash=item.content_hash,
                                   first_seen=timezone.now())  # fmt: skip
    url = "/records/legislature-blue-book-bios/review/"
    page = client.get(url).content.decode()
    assert "Bob Ewing" in page and '<mark class="bg-amber-100 rounded-sm">Sberry</mark>' in page
    assert client.get("/records/legislature-blue-book-bios/images/5067.webp").status_code == 200

    client.post(f"{url}{record.pk}/",
                {"part": "spouse", "from": "ours", "value": "Sherry", "reason": ""})  # fmt: skip
    record.refresh_from_db()
    assert record.data["spouse"] == "Sherry" and record.data["unconfirmed"] == []
    assert record.data["by_hand"]["spouse"] == {
        "from": "ours", "reason": "Checked against the page image.",
        "checked": record.data["by_hand"]["spouse"]["checked"]}  # fmt: skip
    assert "Nothing to check" in client.get(url).content.decode()
