"""Address standardization. The first group is whats-up-in-spearfish's tests, unchanged:
the cases local records actually contain (city permits, OpenGov, agendas, county parcels).
The rest are what statewide use adds."""

from __future__ import annotations

import pytest

pytest.importorskip("usaddress")

from sdplaces import address  # noqa: E402

# --- From whats-up-in-spearfish (tests/test_address.py and tests/test_permits.py) ------------


@pytest.mark.parametrize(
    "a, b",
    [
        ("1725 MAIN ST N", "1725 N Main Street"),  # direction before or after the street
        ("515 27TH ST N.", "515 N 27TH ST"),
        ("2312 Platinum Drive, Spearfish, SD 57783", "2312 PLATINUM DR"),
        (
            "2430 PLATINUM DR, Unit 3, SPEARFISH, SD 57783",
            "2430 Platinum Drive 2",
        ),  # units share the building
        ("4025 E. COLORADO BLVD.", "4025 E COLORADO BLVD"),
        ("6845 SAINT ONGE RD", "6845 ST ONGE RD"),
        ("540 32ND ST-GA OFFICE", "540 32ND ST"),  # tag glued to the street type
        ("20189 US HWY 85-CAMPGROUND", "20189 US Highway 85"),
        ("440 HERITAGE DR, SPEARFISH, SD  57783", "440 Heritage Drive"),  # county SITUS format
    ],
)
def test_same_place(a, b):
    assert address.key(a) == address.key(b)


@pytest.mark.parametrize(
    "a, b",
    [
        ("2312 Platinum Drive, Spearfish, SD 57783", "2312 PLATINUM DR"),
        ("2430 PLATINUM DR, Unit 3, SPEARFISH, SD 57783", "2430 PLATINUM DR"),
        ("2430 Platinum Drive 2", "2430 PLATINUM DR"),
        ("517 N Main Street, Unit STE B", "517 N MAIN ST"),
        ("1 Main St #4", "1 MAIN ST"),
        ("515 27TH ST N.", "515 N 27TH ST"),
        ("100 HWY 14", "100 HWY 14"),
    ],
)
def test_key(a, b):
    assert address.key(a) == b


def test_key_and_loose():
    assert address.key("1440 N MAIN ST") == "1440 N MAIN ST"
    assert address.loose("1440 N MAIN ST") == address.loose("1440 MAIN ST") == "1440 MAIN ST"


def test_units_kept_apart_from_key():
    (a,) = address.parse("300 AVIATION PL, UNIT 254")
    assert a.key() == "300 AVIATION PL" and a.unit == "UNIT 254"


def test_several_addresses_in_one_field():
    assert address.keys("2545 & 2555 CLEAR SPRING RD") == [
        "2545 CLEAR SPRING RD",
        "2555 CLEAR SPRING RD",
    ]
    assert address.keys("3813 & 3815 Rock Ridge, Spearfish, SD 57783") == [
        "3813 ROCK RDG",
        "3815 ROCK RDG",
    ]


@pytest.mark.parametrize(
    "text",
    [
        "",
        "0",
        "Main Street",
        "Intersection of St. Onge Road and HWY 85",
        "City-wide",
        "Spearfish, SD",
    ],
)
def test_not_an_address(text):
    assert address.parse(text) == ()
    assert address.key(text) == ""


# --- Statewide -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "street_key"),
    [
        ("123 CUSTER ST", "123 CUSTER ST"),  # Custer is a city; a street named for it stays
        ("100 LEAD ST", "100 LEAD ST"),
        ("10 W HURON AVE", "10 W HURON AVE"),
        ("1300 FORT PIERRE RD", "1300 FORT PIERRE RD"),
        ("1 ROSE LANE", "1 ROSE LN"),  # Lane is a town (Jerauld County); here it's the street type
        ("1007 SPEARFISH MTN LANE", "1007 SPEARFISH MTN LN"),
        (
            "1010 Christensen Drive Lot A of NW SW of Section 24, T6N, R2E, BHM",
            "1010 CHRISTENSEN DR",
        ),
        ("1103 QUAIL CT", "1103 QUAIL CT"),  # CT is the street type here, not Connecticut
        ("104 W HUDSON", "104 W HUDSON"),  # Hudson is a town, but "104 W" isn't a whole street
        ("2515 Sapphire Lane, Spearfish; Located south of Old Highway 14", "2515 SAPPHIRE LN"),
        ("123 BROADWAY SEATTLE WA 98122", "123 BROADWAY"),
        ("388 Centauri Avenue, S, SD 57783", "388 S CENTAURI AVE"),  # ", S," is a direction
    ],
)
def test_streets_named_for_towns_keep_their_names(text, street_key):
    assert address.key(text) == street_key


@pytest.mark.parametrize(
    ("text", "street_key", "city", "state", "zip_code"),
    [
        (
            "2312 Platinum Drive, Spearfish, SD 57783",
            "2312 PLATINUM DR",
            "SPEARFISH",
            "SD",
            "57783",
        ),
        ("100 MAIN ST HILL CITY", "100 MAIN ST", "HILL CITY", "", ""),  # SD town, no comma
        ("123 CUSTER ST, CUSTER, SD", "123 CUSTER ST", "CUSTER", "SD", ""),
        (
            "500 Nicollet Mall, Minneapolis, MN 55401",
            "500 NICOLLET MALL",
            "MINNEAPOLIS",
            "MN",
            "55401",
        ),
        ("1 N Dakota Ave, Sioux Falls, South Dakota", "1 N DAKOTA AVE", "SIOUX FALLS", "SD", ""),
        (
            "12 MAIN ST, SPEARISH SD",
            "12 MAIN ST",
            "SPEARFISH",
            "SD",
            "",
        ),  # a misspelling seen in records
    ],
)
def test_city_state_and_zip_kept_outside_the_key(text, street_key, city, state, zip_code):
    (a,) = address.parse(text)
    assert (a.key(), a.city, a.state, a.zip) == (street_key, city, state, zip_code)
