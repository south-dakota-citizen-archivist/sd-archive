"""Matching session profiles to the Historical Listing's legislators."""

import pytest

from records.sources._legislature import match
from records.sources._legislature.match import Person


def person(member_id, family, given, *services, initials=None):
    forms = match._forms(given)
    return Person(member_id=member_id, family=family.replace(" ", ""), given=forms,
                  initials=initials or {w[0] for w in given.upper().split()},
                  services=list(services))  # fmt: skip


EVERYONE = (
    person(3830, "BUHL O DONNELL", "Angela Marie", ("S", 2011, 2016, "MINNEHAHA")),
    person(3681, "BEGALKA", "Timothy P Tim", ("H", 2003, 2010, "CLARK")),
    person(3773, "DE VRIES", "Marcus", ("H", 2007, 2008, "JACKSON")),
    person(3766, "LUST", "David", ("H", 2007, 2014, "PENNINGTON"), (None, 2016, 2018, "")),
    person(1, "PETERSON", "Sue", ("H", 2017, 2022, "MINNEHAHA")),
    person(2, "PETERSON", "Susan", ("H", 2017, 2022, "BROOKINGS")),
    person(3, "SMITH", "John", ("H", 2019, 2024, "LAKE")),
    person(4, "SMITH", "John", ("H", 2019, 2024, "LAKE")),
    person(5, "ANDERA", "Bobbi L", ("H", 2025, None, "MINNEHAHA")),
)


def profile(first, last, chamber, year, counties=""):
    return {"first_name": first, "last_name": last, "chamber": chamber, "session_year": year,
            "counties": counties}  # fmt: skip


@pytest.mark.parametrize(("p", "expected", "how"), [
    (profile("Tim", "Begalka", "House", 2005), 3681, "name"),  # a nickname, in quotes
    (profile("Angie", "Buhl", "Senate", 2012), 3830, "family within"),  # married name
    (profile("Mark", "DeVries", "House", 2008), 3773, "name"),  # Mark/Marcus; spacing
    (profile("David", "Lust", "House", 2017), 3766, "name"),  # a service with no body
    (profile("Bobbi", "Andera", "House", 2026), 5, "name"),  # serving now: no end year
    (profile("Sue", "Peterson", "House", 2020, "Minnehaha"), 1, "name"),  # a tie: by county
    (profile("Angela", "Buhl O'Donnell", "Senate", 2017), 3830, "name, a year off"),
])  # fmt: skip
def test_matches(p, expected, how):
    got = match.match(p, EVERYONE)
    assert (got.member_id, got.how) == (expected, how)


def test_nothing_is_guessed():
    # two people of the same name serving together, the county no help
    got = match.match(profile("John", "Smith", "House", 2020, "Lake"), EVERYONE)
    assert got.member_id is None and got.candidates == [3, 4]
    # the right family name, but not serving then
    assert match.match(profile("David", "Lust", "House", 2003), EVERYONE).member_id is None
    # serving then, but another chamber
    assert match.match(profile("David", "Lust", "Senate", 2010), EVERYONE).member_id is None


def test_a_match_carries_to_profiles_with_the_same_member_id(monkeypatch):
    monkeypatch.setattr(match, "crosswalk", lambda: {10: 3830})
    monkeypatch.setattr(match, "people", lambda: EVERYONE)
    profiles = [
        {"session_member_id": 10, "member_id": 700, **profile("Angie", "Buhl", "S", 2012)},
        {"session_member_id": 11, "member_id": 700, **profile("Angie", "Buhl", "S", 2030)},
        {"session_member_id": 12, "member_id": 701, **profile("Bobbi", "Andera", "H", 2026)},
        {"session_member_id": 13, "member_id": 702, **profile("New", "Member", "H", 2027)},
    ]  # fmt: skip
    match.assign_legislators(profiles)
    got = [(p["legislator_id"], p["legislator_id_from"]) for p in profiles]
    assert got == [(3830, "crosswalk"), (3830, "member_id (10)"), (5, "matched (name)"),
                   (None, None)]  # fmt: skip
