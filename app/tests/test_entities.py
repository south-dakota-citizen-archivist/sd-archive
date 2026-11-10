import json

import pytest
from django.core.management import call_command
from django.db import connection, transaction
from django.db.utils import ProgrammingError

from entities import mentions
from entities.management.commands import load_legislator_gold as gold
from entities.models import Mention


def test_one_mention_per_party(db):
    rows = mentions.from_text(
        "PENNOCK, SCOTT R & DOUGLASS, CLYDETTE D CO-TRUSTEES",
        dataset="gis-parcels", record_key="641:17", field="OWNER_NAM",
        url="https://example.gov/parcel/17", properties={"address": ["1 Main St, Lead SD"]},
    )  # fmt: skip
    assert [(m.name, m.position, m.role, m.schema) for m in rows] == [
        ("PENNOCK, SCOTT R", 0, "trustee", "Person"),
        ("DOUGLASS, CLYDETTE D", 1, "trustee", "Person"),
    ]
    assert rows[1].properties == {
        "firstName": ["CLYDETTE"],
        "middleName": ["D"],
        "lastName": ["DOUGLASS"],
        "address": ["1 Main St, Lead SD"],
    }
    assert rows[0].normalized == "PENNOCK SCOTT R"
    assert all(m.original.startswith("PENNOCK") for m in rows)


def test_two_people_can_match_two_entities(db):
    rows = mentions.from_text("HENRY JAMES AND FRANK OCEAN", dataset="x", record_key="1")
    assert [m.properties["lastName"] for m in rows] == [["JAMES"], ["OCEAN"]]


def test_organizations_and_unnamed_parties(db):
    [org] = mentions.from_text("ZBR INVESTMENTS LLC", dataset="x", record_key="1")
    assert org.schema == "Company" and org.properties == {}
    rows = mentions.from_text("THOMPSON,DANA L  ETAL", dataset="x", record_key="2")
    assert len(rows) == 1, "ET AL names no one"


def test_replace_leaves_nothing_stale(db):
    mentions.replace("x", mentions.from_text("JOHN AND MARY SMITH", dataset="x", record_key="1"))
    assert Mention.objects.filter(dataset="x").count() == 2
    mentions.replace("x", mentions.from_text("JOHN SMITH", dataset="x", record_key="1"))
    assert list(Mention.objects.values_list("name", flat=True)) == ["JOHN SMITH"]
    mentions.replace("x", [], keys=["1"])
    assert not Mention.objects.exists()


def test_legislator_gold(db, tmp_path):
    for pid, name in [("1", "Paul Dennert"), ("2724", "Paul Dennert"), ("9", "Jean Hunhoff")]:
        (tmp_path / f"sd-legislature-legislator-{pid}.json").write_text(json.dumps({
            "legislator_profile_id": int(pid), "name": name, "year": "2011", "chamber": "H",
            "district": "02", "party": "Democratic", "address1": "1505 E. Melgaard Rd.",
            "city": "Aberdeen", "state": "SD", "zipcode": "57401",
        }))  # fmt: skip
    xwalk = gold.crosswalk()
    assert len(xwalk) > 4000 and xwalk["758"] == xwalk["2724"], "one person, many profiles"

    call_command("load_legislator_gold", tmp_path)
    m = Mention.objects.get(record_key="2724")
    assert m.gold_entity_id == f"sd-legislator:{xwalk['2724']}"
    assert m.role == "legislator" and m.url.endswith("/SessionMembers/Detail/2724")
    assert m.context["district"] == "02"
    assert m.properties["address"] == ["1505 E. Melgaard Rd., Aberdeen, SD 57401"]
    call_command("load_legislator_gold", tmp_path)
    assert Mention.objects.filter(dataset="legislature").count() == 3, "reloads in place"


def test_export_role_reads_only_the_published_views(db, county):
    with connection.cursor() as cur, transaction.atomic():
        cur.execute("SET LOCAL ROLE sd_export")
        cur.execute("SELECT name, geoidfq FROM published.places")
        assert ("Test County", "0500000US46999") in cur.fetchall()
        cur.execute("SELECT count(*) FROM published.gis_datasets")
        for table in ("places_jurisdiction", "entities_mention"):
            denied = pytest.raises(ProgrammingError, match="permission denied")
            with denied, transaction.atomic():
                cur.execute(f"SELECT * FROM {table}")
