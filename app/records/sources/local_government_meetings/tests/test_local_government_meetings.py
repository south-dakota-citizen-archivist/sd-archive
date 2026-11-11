"""Local government meetings: real portal pages (checked by hand) read, boards tied to their
places, and a run over two boards (one with meetings and files, one with none)."""

from datetime import date
from pathlib import Path

import pytest

import sdfetch
from records.models import Document, Record, RecordSet
from records.run import run_source
from records.sources.local_government_meetings import parse, source

FIXTURES = Path(__file__).parent / "fixtures"


def page(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def test_the_board_list():
    boards = parse.boards(page("letter-s.html"))
    assert len(boards) == 7 and all(b.isdigit() for b, _ in boards)


def test_a_boards_meetings():
    meetings = parse.meetings(page("springfield.html"))
    assert len(meetings) == 11
    nov = next(m for m in meetings if m["date"] == "2026-11-02")
    assert nov["body"] == "Regular Meeting of the Common Council"
    assert nov["start"] == "2026-11-02T18:30:00" and nov["time_zone"] == "Central Standard Time"
    assert nov["location"] == "Springfield Community Services Center"
    assert [(d["description"], d["type"], d["file_id"]) for d in nov["documents"]] == [
        ("Agenda_2November2026", "Agenda", "2507"),
        ("AudioPart1_2November2026", "Audio", "2510"),
        ("AudioPart2_2November2026", "Audio", "2511"),
        ("Packet_2November2026", "Document", "2508"),
    ]  # fmt: skip
    assert nov["documents"][0]["url"] == (
        "https://sdlocalgov.appssd.sd.gov/DownloadFile.ashx?FileID=2507&preview=true"
    )
    assert nov["documents"][0]["uploaded"] == "2026-10-29T19:03:08"
    assert parse.years(page("springfield.html")) == ["2026", "2025", "2024", "2023", "2022"]
    assert parse.meetings(page("aberdeen.html")) == []


def test_board_places():
    assert source.board_place("Lawrence County Commission", "county") == "0500000US46081"
    assert source.board_place("Board of Clay County Commissioners", "county") == "0500000US46027"
    assert source.board_place("Buffalo", "county") == "0500000US46017"
    assert source.board_place("Buffalo", "city") == "1600000US4608140"
    assert source.board_place("Spearfish, City of", "city") == "1600000US4660020"
    assert source.board_place("Viborg-Hurley School District", "school board").startswith(
        "9700000US46")  # fmt: skip
    assert source.board_place("Miller School District", "school board") == source.board_place(
        "Miller Area School District", "school board")  # fmt: skip
    assert source.board_place("Nowhere At All", "city") is None


class FixtureFetcher:
    """The portal, for two boards: Springfield (339; its 2023 archive too) and Aberdeen (89)."""

    store = None

    def __init__(self):
        self.asked = []

    def request(self, method, url, data=None, **kwargs):
        self.asked.append((method, url, (data or {}).get("__EVENTTARGET"),
                           (data or {}).get("ddlArchiveYear")))  # fmt: skip
        if url == parse.SITE and method == "GET":
            body = page("aberdeen.html")  # any page with the header's form
        elif url == parse.SITE:  # the search by kind
            body = (
                b'<table id="gvResults"><tr><td><a href="Meetings.aspx?BoardID=339">'
                b"Springfield</a></td></tr><tr><td>"
                b'<a href="Meetings.aspx?BoardID=89">Aberdeen</a></td></tr></table>'
                if data["header$ddlAgency"] == "3"
                else b'<span id="noresults"></span>'
            )
        elif url == parse.ALL:
            body = (
                b'<table id="gvResults"><tr><td><a href="Meetings.aspx?BoardID=339">'
                b'Springfield</a></td></tr><tr><td><a href="Meetings.aspx?BoardID=89">'
                b"Aberdeen</a></td></tr></table>"
            )
        elif url.endswith("BoardID=339"):
            year = (data or {}).get("ddlArchiveYear")
            body = page("springfield-2023.html") if year == "2023" else page("springfield.html")
        elif url.endswith("BoardID=89"):
            body = page("aberdeen.html")
        else:
            raise AssertionError(url)
        return type("R", (), {"content": body})()


def test_run(db, sd_places):
    RecordSet.objects.create(slug="local-government-meetings", name="Meetings",
                             dataset="local-government-meetings")  # fmt: skip
    fetcher = FixtureFetcher()
    run = run_source("local-government-meetings", fetcher=fetcher)
    # 11 meetings on the first page, and 2023's 8 not among them (the upcoming ones repeat)
    years = [y for _, url, target, y in fetcher.asked if target == "ddlArchiveYear"]
    assert years == ["2025", "2024", "2023", "2022"]
    assert run.added == Record.objects.count() > 11
    assert run.checks["found"]["boards: city"] == 2
    assert run.checks["found"]["boards with meetings"] == 1
    nov = Record.objects.get(data__date="2026-11-02")
    assert nov.data["board"] == "Springfield" and nov.data["board_kind"] == "city"
    assert nov.places.filter(kind="place", short_name="Springfield").exists()
    # the agenda and packet are documents to download; the recordings aren't
    assert sorted(Document.objects.filter(record=nov).values_list("metadata__type", flat=True)) == [
        "Agenda", "Document"]  # fmt: skip
    assert len(nov.data["documents"]) == 4


@pytest.mark.vcr
def test_the_live_site(tmp_path):
    fetcher = sdfetch.Fetcher(tmp_path, delay=0)
    pages = source.board_pages(fetcher, "339", date(2026, 11, 11))
    assert len(pages) == len(parse.years(pages[0]))
    meetings = [m for p in pages for m in parse.meetings(p)]
    assert meetings and all(m["meeting_id"].isdigit() and m["date"] for m in meetings)
