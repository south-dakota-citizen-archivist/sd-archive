import tomllib

from django.db import connection, transaction
from django.utils import timezone

from census import seed
from census.models import Dataset, Publisher, Source


def make_watched(source, url, **kwargs):
    now = timezone.now()
    return Dataset.objects.create(
        source=source,
        url=url,
        name="Parcels",
        status=Dataset.Status.WATCHED,
        first_seen=now,
        last_seen=now,
        **kwargs,
    )


def test_dump_round_trips(source, county, seed_file):
    source.folder_jurisdictions = {"zeta": "test-county", "Alpha": "test-county"}
    source.crawl_schedule = ""  # never crawl on a schedule: a blank that must survive
    source.max_workers = 4
    source.save()
    make_watched(
        source,
        f"{source.url}/Parcels/FeatureServer/0",
        max_drop=0.25,
        name_fields=["OWNER"],
        name_fields_manual=True,
    )
    Source.objects.create(name="Map app", slug="b", url="https://b.example", platform="map_app")

    assert seed.dump() is True
    assert seed.dump() is False, "unchanged content isn't rewritten"
    data = tomllib.loads(seed_file.read_text())

    [entry] = data["sources"]
    assert entry["slug"] == "test-server" and entry["publisher"] == "Test County GIS"
    assert entry["crawl_schedule"] == "" and entry["max_workers"] == 4
    assert "crawl_counts" not in entry, "defaults are left out"
    assert list(entry["folder_jurisdictions"]) == ["Alpha", "zeta"]
    assert [p["name"] for p in data["publishers"]] == ["Test County GIS"]
    assert data["watch"] == [
        {
            "url": f"{source.url}/Parcels/FeatureServer/0",
            "max_drop": 0.25,
            "name_fields": ["OWNER"],
        }
    ]

    # Change things in the app, then load the file back
    Source.objects.filter(pk=source.pk).update(max_workers=1, folder_jurisdictions={})
    Dataset.objects.update(status=Dataset.Status.DISCOVERED, max_drop=0.1)
    seed.load(seed_file)
    source.refresh_from_db()
    assert source.max_workers == 4 and source.crawl_schedule == ""
    assert source.folder_jurisdictions == {"zeta": "test-county", "Alpha": "test-county"}
    dataset = Dataset.objects.get()
    assert dataset.status == Dataset.Status.WATCHED and dataset.max_drop == 0.25


def test_dump_keeps_watch_entries_not_crawled_yet(source, seed_file):
    seed_file.write_text('[[watch]]\nurl = "https://elsewhere.example/FeatureServer/0"\n')
    seed.dump()
    urls = [w["url"] for w in tomllib.loads(seed_file.read_text())["watch"]]
    assert urls == ["https://elsewhere.example/FeatureServer/0"]


def test_changes_rewrite_the_file_after_commit(
    county, seed_file, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        publisher = Publisher.objects.create(name="New publisher")
        source = Source.objects.create(
            name="New",
            slug="new",
            url="https://new.example/rest/services",
            platform="arcgis_server",
            publisher=publisher,
        )
    assert len(callbacks) == 1, "one rewrite for both changes"
    assert "New publisher" in seed_file.read_text()
    connection.run_on_commit.clear()  # the test's transaction never commits

    with django_capture_on_commit_callbacks() as callbacks, transaction.atomic():
        for n in range(5):
            source.notes = f"edit {n}"
            source.save()
    assert len(callbacks) == 1, "one rewrite per transaction"

    with django_capture_on_commit_callbacks() as callbacks:
        source.last_crawled_at = timezone.now()
        source.save(update_fields=["last_crawled_at"])
        dataset = make_watched(source, f"{source.url}/x/FeatureServer/0")
    assert len(callbacks) == 1, "only the new watched dataset counts"

    with django_capture_on_commit_callbacks() as callbacks:
        dataset.last_error = "timeout"
        dataset.save(update_fields=["last_error"])
    assert callbacks == []


def test_load_doesnt_rewrite_the_file(county, seed_file, django_capture_on_commit_callbacks):
    seed_file.write_text('[[publishers]]\nname = "Loaded"\n')
    with django_capture_on_commit_callbacks() as callbacks:
        seed.load(seed_file)
    assert Publisher.objects.filter(name="Loaded").exists()
    assert callbacks == []
