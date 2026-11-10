from datetime import date

import geopandas as gpd
import pytest
from shapely.geometry import MultiPolygon, Polygon, box

import sdplaces
from places import load
from places.models import Jurisdiction

SQ = box(-104, 44, -103, 45)
TOWN = MultiPolygon([box(-103.9, 44.4, -103.8, 44.5)])
# a shape with Z coordinates, as some years' files have
SHANNON_3D = Polygon([(-103, 43, 0), (-102, 43, 0), (-102, 44, 0), (-103, 44, 0)])


def place(kind, geoid, name, slug, parent="", **extra) -> sdplaces.Place:
    prefix = {"state": "0400000US", "county": "0500000US", "place": "1600000US"}[kind]
    return sdplaces.Place(
        kind=kind, geoid=geoid, geoidfq=prefix + geoid, name=name, short_name=name.split()[0],
        slug=slug, parent=parent, lat=None, lon=None,
        ocd_id=f"ocd-division/country:us/state:sd/{kind}:{slug}",
        valid_from=extra.get("valid_from"), valid_to=extra.get("valid_to"),
        successor=extra.get("successor", ""),
    )  # fmt: skip


PLACES = {
    "state": [place("state", "46", "South Dakota", "south-dakota")],
    "county": [
        place("county", "46081", "Lawrence County", "lawrence-county", "46"),
        place("county", "46102", "Oglala Lakota County", "oglala-lakota-county", "46"),
        place(
            "county",
            "46113",
            "Shannon County",
            "shannon-county",
            "46",
            valid_to=date(2015, 5, 1),
            successor="46102",
        ),
        place(
            "county",
            "46131",
            "Washabaugh County",
            "washabaugh-county",
            "46",
            valid_to=date(1983, 1, 1),
        ),
    ],  # fmt: skip
    "place": [place("place", "4660020", "Spearfish city", "spearfish", "46081")],
}


def frame(geoms: dict) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame({"geoid": list(geoms)}, geometry=list(geoms.values()), crs=4326)


@pytest.fixture
def fake_sdplaces(monkeypatch):
    current = {"state": {"46": SQ}, "county": {"46081": SQ, "46102": SQ},
               "place": {"4660020": TOWN}}  # fmt: skip

    def boundaries(kind, year=None):
        if year is None:
            return frame(current[kind])
        if kind == "county" and year == 2014:
            return frame({"46113": SHANNON_3D})  # only an old file has Shannon
        return frame({})

    monkeypatch.setattr(load.sdplaces, "boundaries", boundaries)
    monkeypatch.setattr(load.sdplaces, "all_places", lambda kind: PLACES[kind])


KINDS = ("state", "county", "place")


def test_load_sets_parents_ids_and_successors(db, fake_sdplaces):
    assert load.load(KINDS) == {"state": 1, "county": 4, "place": 1}
    spearfish = Jurisdiction.objects.get(kind="place", geoid="4660020")
    assert spearfish.parent.slug == "lawrence-county"
    assert spearfish.parent.parent.kind == "state"
    assert spearfish.geoidfq == "1600000US4660020"
    assert spearfish.ocd_id.endswith("place:spearfish")
    assert spearfish.boundary.geom_type == "MultiPolygon"

    shannon = Jurisdiction.objects.get(geoidfq="0500000US46113")
    assert shannon.valid_to == date(2015, 5, 1)
    assert shannon.successor.name == "Oglala Lakota County"
    assert shannon.boundary is not None and not shannon.boundary.hasz, "from the 2014 file, 2D"
    assert Jurisdiction.objects.get(slug="washabaugh-county").boundary is None

    current = Jurisdiction.objects.current().filter(kind="county")
    assert sorted(current.values_list("geoid", flat=True)) == ["46081", "46102"]


def test_load_again_updates_in_place(db, fake_sdplaces):
    load.load(KINDS)
    load.load(KINDS)
    assert Jurisdiction.objects.count() == 6


def test_governments_leave_out_districts(db):
    for kind, geoid in [("county", "46081"), ("school_district", "4666270"), ("sldu", "46019")]:
        Jurisdiction.objects.create(
            kind=kind,
            geoid=geoid,
            geoidfq=f"{kind}{geoid}",
            name=geoid,
            short_name=geoid,
            slug=f"{kind}-{geoid}",
        )
    assert list(Jurisdiction.objects.governments().values_list("kind", flat=True)) == ["county"]
