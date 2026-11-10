import pytest
from django.db import connection

from harvest import service
from search import index


def normalize(value: str) -> str:
    with connection.cursor() as cur:
        cur.execute("SELECT gis_hub_normalize_name(%s)", [value])
        return cur.fetchone()[0]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Smith, José  & JANE—Trust", "SMITH JOSE JANE TRUST"),
        ("  o'brien ", "O BRIEN"),
        ("WHARF RESOURCES (U.S.A.) INC", "WHARF RESOURCES U S A INC"),
    ],
)
def test_normalize_function(db, raw, expected):
    assert normalize(raw) == expected


@pytest.fixture
def harvested(dataset, server):
    server.owners.update({1: "WHARF RESOURCES (U.S.A.) INC", 2: "WHARF RESOURCES (U.S.A.) INC"})
    server.owners[3] = "JOHNSON, MARK A"
    server.owners[5] = "JOHNSON, MARILYN"
    service.harvest_dataset(dataset)
    return dataset


def test_search_groups_by_name(harvested):
    results = index.search("wharf resources")
    top = results[0]
    assert top["name"] == "WHARF RESOURCES (U.S.A.) INC"
    assert top["n_records"] == 2
    assert {r["feature_key"] for r in top["records"]} == {"1", "2"}
    assert top["records"][0]["url"] == f"/datasets/{harvested.pk}/features/1/"


def test_all_words_rank_above_fuzzy(harvested):
    names = [g["name"] for g in index.search("mark johnson")]
    assert names[0] == "JOHNSON, MARK A"
    assert "JOHNSON, MARILYN" in names


def test_former_owners_are_found(harvested, server):
    server.owners[3] = "DOE, JANE"
    service.harvest_dataset(harvested)

    [match] = [g for g in index.search("johnson mark") if g["name"] == "JOHNSON, MARK A"]
    assert not match["has_current"]
    assert match["records"][0]["valid_to"] is not None
    assert not [
        g for g in index.search("johnson mark", current_only=True) if g["name"] == "JOHNSON, MARK A"
    ]


def test_short_queries_return_nothing(harvested):
    assert index.search("jo") == []


def test_cross_project_view(harvested):
    with connection.cursor() as cur:
        cur.execute(
            "SELECT project, record_type, name, normalized_name, url, context->>'field' "
            "FROM search_names WHERE normalized_name = 'JOHNSON MARK A'"
        )
        row = cur.fetchone()
    assert row == (
        "sd-gis-hub",
        "gis_feature",
        "JOHNSON, MARK A",
        "JOHNSON MARK A",
        f"/datasets/{harvested.pk}/features/3/",
        "OWNER_NAM",
    )


def test_rebuild_with_no_name_fields_clears_index(harvested):
    harvested.name_fields = []
    harvested.save()
    assert index.rebuild(harvested) == 0
    assert not harvested.name_entries.exists()
