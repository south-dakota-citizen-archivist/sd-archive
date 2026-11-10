from collections import Counter
from datetime import date

import pytest

import sdplaces
from sdplaces import AmbiguousPlace, NotInForce, UnknownPlace

# --- The table ---------------------------------------------------------------------------


def test_sixty_six_counties_today():
    assert len(sdplaces.counties()) == 66
    assert len(sdplaces.counties(include_historical=True)) == 68


def test_no_geoid_used_twice_within_a_kind():
    pairs = Counter((p.kind, p.geoid) for p in sdplaces.all_places())
    assert [k for k, n in pairs.items() if n > 1] == []


def test_fully_qualified_ids_are_unique():
    ids = Counter(p.geoidfq for p in sdplaces.all_places())
    assert [k for k, n in ids.items() if n > 1] == []


def test_every_city_and_cdp_has_a_parent_county():
    counties = {p.geoid for p in sdplaces.counties(include_historical=True)}
    for p in sdplaces.all_places("place") + sdplaces.all_places("cdp"):
        assert p.parent in counties, p


def test_historical_places_have_an_end_date_and_a_real_successor():
    for p in sdplaces.all_places():
        if p.successor:
            assert p.valid_to is not None, p
            assert sdplaces.get(p.successor, p.kind), p


def test_incorporated_places_match_the_gis_hub():
    assert len(sdplaces.all_places("place")) == 310


# --- Lookups -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "Lawrence",
        "LAWRENCE COUNTY",
        "Lawrence Co.",
        "lawrence county, sd",
        "Lawrence County, South Dakota",
    ],
)
def test_county_names_however_written(name):
    assert sdplaces.county(name).geoid == "46081"


@pytest.mark.parametrize(
    ("name", "geoid"),
    [("McCook", "46087"), ("Mc Cook", "46087"), ("Bon Homme", "46009"), ("BonHomme", "46009"),
     ("Fall River", "46047"), ("Fall River County", "46047"), ("Charles Mix", "46023")],
)  # fmt: skip
def test_spacing_and_compound_names(name, geoid):
    assert sdplaces.county(name).geoid == geoid


def test_cities():
    spearfish = sdplaces.place("Spearfish")
    assert (spearfish.geoid, spearfish.parent) == ("4660020", "46081")
    assert sdplaces.place("City of Spearfish") == spearfish
    assert sdplaces.place("SPEARFISH CITY") == spearfish
    assert sdplaces.place("Fort Pierre") == sdplaces.place("Ft. Pierre")


def test_get_by_geoid():
    assert sdplaces.get("46081").name == "Lawrence County"
    assert sdplaces.get("4660020").short_name == "Spearfish"
    with pytest.raises(UnknownPlace):
        sdplaces.get("99999")


def test_children():
    lawrence = sdplaces.county("Lawrence")
    towns = {p.short_name for p in sdplaces.children(lawrence, kind="place")}
    assert {"Spearfish", "Lead", "Deadwood", "Whitewood"} <= towns


# --- Historical counties ------------------------------------------------------------------


def test_shannon_and_oglala_lakota():
    shannon = sdplaces.county("Shannon")
    assert (shannon.geoid, shannon.valid_to, shannon.successor) == (
        "46113",
        date(2015, 5, 1),
        "46102",
    )
    assert sdplaces.county("Shannon", current=True).geoid == "46102"
    assert sdplaces.county("Oglala Lakota").geoid == "46102"
    assert sdplaces.county("Oglala Lakota").valid_from == date(2015, 5, 1)


def test_as_of_picks_the_county_in_force():
    assert sdplaces.county("Shannon", as_of="2010-11-02").geoid == "46113"
    with pytest.raises(NotInForce):
        sdplaces.county("Shannon", as_of="2020-11-03")
    with pytest.raises(NotInForce):
        sdplaces.county("Oglala Lakota", as_of="2010-11-02")
    names_2010 = {p.short_name for p in sdplaces.counties(as_of="2010-01-01")}
    assert "Shannon" in names_2010 and "Oglala Lakota" not in names_2010
    assert len(names_2010) == 66


def test_washabaugh_merged_into_jackson():
    washabaugh = sdplaces.county("Washabaugh")
    assert washabaugh.successor == "46071"
    assert sdplaces.current(washabaugh).name == "Jackson County"
    assert len(sdplaces.counties(as_of="1980-01-01")) == 67  # Washabaugh and Shannon both in force


# --- Strict matching -----------------------------------------------------------------------


def test_unknown_names_raise_with_suggestions_and_never_guess():
    with pytest.raises(UnknownPlace) as e:
        sdplaces.county("Lawrense")
    assert "Lawrence" in e.value.suggestions
    with pytest.raises(UnknownPlace):
        sdplaces.county("")


def test_white_horse_and_whitehorse_are_different_places():
    assert sdplaces.lookup("cdp", "White Horse").parent == "46121"  # Todd County
    assert sdplaces.lookup("cdp", "Whitehorse").parent == "46041"  # Dewey County
    assert sdplaces.lookup("cdp", "WHITE-HORSE").parent == "46121"  # a hyphen counts as a space
    # A spacing typo matches neither spelling exactly; ignoring spaces fits both, so it raises
    with pytest.raises(AmbiguousPlace) as e:
        sdplaces.lookup("cdp", "Whi tehorse")
    assert {p.parent for p in e.value.candidates} == {"46121", "46041"}
    assert sdplaces.lookup("cdp", "Whi tehorse", within="Dewey").geoid == "4671140"


def test_same_name_different_kinds():
    assert sdplaces.place("Clear Lake").parent == "46039"  # the city, Deuel County
    assert sdplaces.lookup("cdp", "Clear Lake").parent == "46091"  # the CDP, Marshall County


# --- pandas helper ----------------------------------------------------------------------------


def test_add_geoid_by_date():
    pd = pytest.importorskip("pandas")
    df = pd.DataFrame(
        {
            "county": ["Shannon", "Oglala Lakota", "Lawrence"],
            "date": ["2012-06-05", "2020-11-03", "2020-11-03"],
        }
    )
    out = sdplaces.add_geoid(df, "county", date_column="date")
    assert out["county_geoid"].tolist() == ["46113", "46102", "46081"]
    current = sdplaces.add_geoid(df, "county", current=True)
    assert current["county_geoid"].tolist() == ["46102", "46102", "46081"]


def test_add_geoid_reports_every_problem_at_once():
    pd = pytest.importorskip("pandas")
    df = pd.DataFrame({"county": ["Lawrence", "Lawrense", "Nowhere"]})
    with pytest.raises(UnknownPlace) as e:
        sdplaces.add_geoid(df, "county")
    assert "2 value(s)" in str(e.value)


# --- Crosswalks -------------------------------------------------------------------------------


def test_election_county_codes():
    assert sdplaces.from_id("sos_election_county", 57).name == "Spink County"
    assert sdplaces.from_id("sos_election_county", "07").name == "Yankton County"  # "07" == 7
    assert sdplaces.from_id("sos_election_county", 1).name == "Minnehaha County"
    assert sdplaces.from_id("sos_election_county", 65).name == "Oglala Lakota County"
    with pytest.raises(UnknownPlace):
        sdplaces.from_id("sos_election_county", 66)  # not used by the election system


def test_every_crosswalk_row_points_at_a_real_place():
    import csv
    from importlib import resources

    folder = resources.files("sdplaces").joinpath("data/crosswalks")
    for f in folder.iterdir():
        if f.name.endswith(".csv"):
            with f.open(newline="") as fh:
                for row in csv.DictReader(fh):
                    assert sdplaces.get(row["geoid"]), (f.name, row)
                    assert row["evidence"], (f.name, row)


def test_election_crosswalk_covers_every_county():
    geoids = {sdplaces.from_id("sos_election_county", i).geoid for i in [*range(1, 66), 67]}
    assert geoids == {p.geoid for p in sdplaces.counties()}


def test_unknown_crosswalk_system():
    with pytest.raises(ValueError):
        sdplaces.from_id("nope", 1)
