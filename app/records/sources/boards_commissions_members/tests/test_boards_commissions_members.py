"""Board and commission members: the parser against real pages (the board list and five
boards, checked by hand), one board against a recording of the site, and a run into the
database. Re-record when the site changes:
just record app/records/sources/boards_commissions_members"""

from pathlib import Path

import pytest

import sdfetch
from entities.models import Mention
from records.models import Record, RecordSet
from records.run import run_source
from records.sources.boards_commissions_members import parse, source

FIXTURES = Path(__file__).parent / "fixtures"
BOARDS = (2, 13, 100, 123, 215)


def page(name: str) -> bytes:
    return (FIXTURES / f"{name}.html").read_bytes()


def members(board_id: int):
    return {m.name: m for m in parse.members(page(f"board-{board_id}"), board_id, source.SITE)}


def test_board_list():
    ids = parse.board_ids(page("board-list"))
    assert len(ids) == 145 and len(set(ids)) == 145
    assert ids[:4] == [2, 3, 4, 29]


def test_members_and_their_board():
    got = members(2)
    assert len(got) == 11
    chase = got["Aimee Chase"]
    assert (chase.board_name, chase.agency_affiliation, chase.board_phone, chase.board_email) == (
        "911 Coordination Board",
        "Department of Public Safety",
        "605-773-3264",
        "SD911Coordinator@state.sd.us",
    )
    assert (chase.city, chase.city_place, str(chase.term_end)) == (
        "Sioux Falls",
        "1600000US4659020",
        "2026-06-30",
    )
    assert got["Michelle DeNeui"].bio == "South Dakota Municipal League"  # "Biography:" dropped


def test_party_picture_and_no_term():
    got = members(13)
    johnson = got["Jarrod Johnson"]
    assert (johnson.party, johnson.picture_url) == (
        "Republican",
        "https://boardsandcommissions.sd.gov/bcuploads/Jarrod%20Johnson.jpg",
    )
    assert (got["Kory Bierle"].term_end, got["Kory Bierle"].term_end_raw) == (None, "")


def test_labels_are_dropped_as_words_not_letters():
    # "Agency Affiliation: Attorney General" once lost its leading letters ("rney General")
    assert {m.agency_affiliation for m in members(215).values()} == {"Attorney General"}
    # and "Biography: https://…" its h
    assert members(123)["Jamalia Franzen"].bio == (
        "https://sdsos.gov/about-the-office/board-of-elections/default.aspx"
    )


def test_a_board_without_members():
    assert members(100) == {}


def test_a_changed_layout_fails_loudly():
    with pytest.raises(parse.LayoutChanged):
        parse.board_ids(b"<html><body><p>Down for maintenance</p></body></html>")
    with pytest.raises(parse.LayoutChanged):
        parse.members(page("board-2").replace(b'id="boardTitle"', b'id="x"'), 2, source.SITE)


class FixtureFetcher:
    """The board list, cut to the five boards with fixtures."""

    def request(self, method, url, **kwargs):
        class Resp:
            pass

        r = Resp()
        if url == source.LIST_URL:
            links = "".join(f'<a href="boardmembers.aspx?BoardID={i}">{i}</a>' for i in BOARDS)
            r.content = f'<table id="gvResults"><tr><td>{links}</td></tr></table>'.encode()
        else:
            r.content = page(f"board-{url.rsplit('=', 1)[1]}")
        return r


def test_run_stores_members_places_and_mentions(db, state):
    from places.models import Jurisdiction

    Jurisdiction.objects.create(kind="place", geoid="4660020", geoidfq="1600000US4660020",
                                name="Spearfish city", short_name="Spearfish", slug="spearfish",
                                parent=state)  # fmt: skip
    for geoidfq in {m.city_place for b in BOARDS for m in members(b).values()} - {
            None, "1600000US4660020"}:  # fmt: skip
        Jurisdiction.objects.create(
            kind="place", geoid=geoidfq[-7:], geoidfq=geoidfq, name=geoidfq,
            short_name=geoidfq, slug=geoidfq.lower(),
        )  # fmt: skip
    RecordSet.objects.create(slug="boards-commissions-members", name="Board members",
                             dataset="boards-commissions-members")  # fmt: skip
    run = run_source("boards-commissions-members", fetcher=FixtureFetcher())
    # 15 on board 215, one listed twice
    assert run.fetched_count == 11 + 5 + 0 + 7 + 14
    deneui = Record.objects.get(key="2 Michelle DeNeui 2027-06-30")
    assert sorted(p.geoidfq for p in deneui.places.all()) == ["0400000US46", "1600000US4660020"]
    assert deneui.source_url == source.board_url(2)
    mention = Mention.objects.get(record_key=f"boards-commissions-members:{deneui.key}")
    assert (mention.name, mention.role, mention.context["board"]) == (
        "Michelle DeNeui",
        "board member",
        "911 Coordination Board",
    )
    assert Record.objects.filter(key="13 Kory Bierle no term").exists()


@pytest.mark.vcr
def test_the_live_site(tmp_path):
    fetcher = sdfetch.Fetcher(tmp_path, delay=0)
    ids = parse.board_ids(fetcher.request("GET", source.LIST_URL).content)
    assert len(ids) >= 140
    got = parse.members(fetcher.request("GET", source.board_url(2)).content, 2, source.SITE)
    assert got and all(m.board_name == "911 Coordination Board" for m in got)
