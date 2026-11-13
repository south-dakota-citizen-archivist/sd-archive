"""Property sales linked to the GIS features of the parcels they list."""

import pytest
from django.core.management import call_command

from records import links


def test_normalize():
    assert links.normalize(" r16374 ") == "R16374"
    assert links.normalize("065514") == links.normalize("65514") == "65514"
    assert links.normalize("17000-00503-280-00") == "17000-00503-280-00"
    assert links.normalize(None) == ""


@pytest.fixture
def made(db, county, dataset, monkeypatch):
    from records.gis import recordset_for
    from records.models import Record, RecordSet, Run

    monkeypatch.setattr(links, "LAYERS", {dataset.url: ("PIN", county.geoid)})
    sets = {"layer": recordset_for(dataset),
            "app": RecordSet.objects.create(slug="gis-data", name="GIS data", dataset="gis-data"),
            "sales": RecordSet.objects.create(slug="property-sales", name="Sales",
                                              dataset="property-sales")}  # fmt: skip

    def record(kind, key, data, places=(county,)):
        rs = sets[kind]
        run = Run.objects.filter(recordset=rs).first() or Run.objects.create(recordset=rs)
        r = Record.objects.create(recordset=rs, key=key, data=data, content_hash=key, run=run,
                                  first_seen=run.started_at)  # fmt: skip
        r.places.set(places)
        return r

    return record


def test_sales_link_to_features(made, county):
    from records.models import RecordLink

    piece1 = made("layer", "1", {"PIN": "065514"})
    piece2 = made("layer", "2", {"PIN": "065514"})  # one parcel in two pieces
    feature = made("app", "App:20600-00602-220-66",
                   {"key": "20600-00602-220-66",
                    "sections": {"Summary": {"Quick Ref ID": "R20891"}}})  # fmt: skip
    made("layer", "3", {"PIN": "77"})
    made("layer", "4", {"PIN": "0077"})  # 77 and 0077: two IDs that read alike
    old = made("sales", "2021:1", {"parcels": ["65514", "20600-00602-220-66"]})
    new = made("sales", "2023:1", {"parcels": ["r20891"]})
    made("sales", "2023:2", {"parcels": ["77", "99999"]})
    made("sales", "2023:3", {"parcels": ["65514"]}, places=())  # no county: not linked

    call_command("link_records")
    got = {(x.from_record_id, x.to_record_id, x.to_field, x.value)
           for x in RecordLink.objects.all()}  # fmt: skip
    assert got == {
        (old.pk, piece1.pk, "PIN", "65514"),
        (old.pk, piece2.pk, "PIN", "65514"),
        (old.pk, feature.pk, "key", "20600-00602-220-66"),
        (new.pk, feature.pk, "Quick Ref ID", "r20891"),
    }
    out = links.link_sales(dry_run=True)
    assert out.ids == {"linked": 3, "ambiguous": 1, "not found": 1}
    # a rebuild replaces the links, not adds to them
    call_command("link_records")
    assert RecordLink.objects.count() == 4
