from pathlib import Path

import geopandas as gpd
import pytest

from harvest import service, validate
from harvest.models import Feature, Harvest
from search.models import NameEntry
from tests.conftest import IDS


def current(dataset) -> dict[str, Feature]:
    return {f.key: f for f in Feature.objects.filter(dataset=dataset, valid_to=None)}


def test_first_harvest(dataset, media_root):
    harvest = service.harvest_dataset(dataset)

    assert harvest.status == Harvest.Status.SUCCEEDED
    assert (harvest.expected_count, harvest.fetched_count, harvest.added) == (10, 10, 10)
    assert harvest.checks["count_matches_server"] is True

    features = current(dataset)
    assert sorted(int(k) for k in features) == IDS
    first = features["1"]
    assert first.properties == {"OBJECTID": 1, "NAME": "Town 1", "OWNER_NAM": "SMITH, JOHN 1"}
    assert first.geometry.geom_type == "Point"
    assert first.geometry.srid == 4326

    export = gpd.read_parquet(Path(media_root) / harvest.export.name)
    assert list(export["OBJECTID"]) == IDS

    dataset.refresh_from_db()
    assert dataset.last_harvested_at is not None
    assert dataset.feature_count == 10


def test_unchanged_harvest_writes_nothing(dataset):
    service.harvest_dataset(dataset)
    second = service.harvest_dataset(dataset)

    assert (second.added, second.changed, second.removed, second.unchanged) == (0, 0, 0, 10)
    assert not second.export, "no new snapshot when nothing changed"
    assert Feature.objects.filter(dataset=dataset).count() == 10


def test_changes_keep_history(dataset, server):
    first = service.harvest_dataset(dataset)
    server.owners[2] = "DOE, JANE"  # sold
    server.moved.add(3)  # reshaped
    server.ids = [i for i in IDS if i != 5] + [144]  # 5 deleted, 144 new

    second = service.harvest_dataset(dataset)

    assert (second.added, second.changed, second.removed, second.unchanged) == (1, 2, 1, 7)
    assert second.export, "changed data gets a new snapshot"

    versions = list(Feature.objects.filter(dataset=dataset, key="2").order_by("valid_from"))
    assert [v.properties["OWNER_NAM"] for v in versions] == ["SMITH, JOHN 2", "DOE, JANE"]
    assert versions[0].valid_to == second.started_at
    assert versions[0].closed_by == second
    assert versions[1].valid_to is None and versions[1].created_by == second

    gone = Feature.objects.get(dataset=dataset, key="5")
    assert gone.valid_to is not None and gone.closed_by == second
    assert "144" in current(dataset)
    assert len(current(dataset)) == 10

    # Point in time: as of the first harvest, the old owner and record 5 were current
    as_of = first.started_at
    then = Feature.objects.filter(dataset=dataset, valid_from__lte=as_of).exclude(
        valid_to__lte=as_of
    )
    assert {f.key for f in then} == {str(i) for i in IDS}


def test_count_mismatch_fails_and_rolls_back(dataset, server, media_root):
    server.reported_count = 5000
    with pytest.raises(validate.ValidationError, match="server reported 5000"):
        service.harvest_dataset(dataset)

    harvest = Harvest.objects.get(dataset=dataset)
    assert harvest.status == Harvest.Status.FAILED
    assert Feature.objects.filter(dataset=dataset).count() == 0
    assert not list(Path(media_root).rglob("*.parquet"))
    dataset.refresh_from_db()
    assert "server reported 5000" in dataset.last_error


def test_failure_after_store_rolls_back_features(dataset, monkeypatch):
    def broken_export(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(service.export, "write_parquet", broken_export)
    with pytest.raises(OSError):
        service.harvest_dataset(dataset)
    assert Feature.objects.filter(dataset=dataset).count() == 0, "all or nothing"


def test_drop_guard(dataset, server):
    service.harvest_dataset(dataset)
    server.ids = IDS[:5]

    with pytest.raises(validate.ValidationError, match="fell from 10 to 5"):
        service.harvest_dataset(dataset)
    assert len(current(dataset)) == 10

    forced = service.harvest_dataset(dataset, force=True)
    assert forced.forced and forced.removed == 5
    assert len(current(dataset)) == 5


def test_drop_guard_per_dataset_threshold(dataset, server):
    service.harvest_dataset(dataset)
    server.ids = IDS[:5]
    dataset.max_drop = 0.6
    dataset.save()
    assert service.harvest_dataset(dataset).removed == 5


@pytest.mark.parametrize(
    ("previous", "current_count", "max_drop", "fails"),
    [(None, 0, 0.1, False), (0, 0, 0.1, False), (100, 90, 0.1, False), (100, 89, 0.1, True)],
)
def test_check_drop(previous, current_count, max_drop, fails):
    if fails:
        with pytest.raises(validate.ValidationError):
            validate.check_drop(previous, current_count, max_drop)
    else:
        validate.check_drop(previous, current_count, max_drop)


def test_table_without_oid_keys_by_content(dataset, server):
    server.has_oid = False
    server.pagination = True
    dataset.object_id_field = ""
    dataset.save()
    service.harvest_dataset(dataset)
    keys = list(current(dataset))
    assert len(keys) == 10 and all(len(k) == 64 for k in keys)


def test_names_indexed_with_history(dataset, server):
    service.harvest_dataset(dataset)
    assert NameEntry.objects.filter(dataset=dataset).count() == 10

    server.owners[2] = "DOE, JANE"
    service.harvest_dataset(dataset)

    former = NameEntry.objects.get(dataset=dataset, normalized="SMITH JOHN 2")
    assert former.valid_to is not None
    now = NameEntry.objects.get(dataset=dataset, normalized="DOE JANE")
    assert now.valid_to is None and now.feature_key == "2"


def test_a_failed_harvest_keeps_its_batches_and_the_next_reuses_them(dataset, server, monkeypatch):
    write_parquet = service.export.write_parquet

    def broken_export(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(service.export, "write_parquet", broken_export)
    with pytest.raises(OSError):
        service.harvest_dataset(dataset)
    staged = service.staging_dir(dataset)
    assert len(list(staged.glob("batch-*.pkl"))) == 3, "the download survives the failure"

    monkeypatch.setattr(service.export, "write_parquet", write_parquet)
    server.calls.clear()
    harvest = service.harvest_dataset(dataset)
    assert harvest.status == Harvest.Status.SUCCEEDED and harvest.added == 10
    assert server.query_calls() == [], "every batch came from staging"
    assert not staged.exists(), "removed once the harvest is stored"
