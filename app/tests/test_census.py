import pytest
from django.utils import timezone

from census import classify, crawl, duplicates, seed
from census.models import (
    Crawl,
    Dataset,
    DatasetTopic,
    Jurisdiction,
    Publisher,
    Service,
    Source,
    Topic,
)
from sdgis.arcgis import client, directory
from sdgis.testing import ROOT, ROUTES, FakeDirectory


@pytest.fixture
def directory_source(county, monkeypatch):
    fake = FakeDirectory()
    monkeypatch.setattr(client, "make_session", lambda *a, **kw: fake)
    other = Jurisdiction.objects.create(
        kind="county", geoid="46998", name="Other County", short_name="Other", slug="other-county"
    )
    source = Source.objects.create(
        name="Example",
        slug="example",
        url=ROOT,
        platform="arcgis_server",
        jurisdiction=county,
        folder_jurisdictions={"County": other.slug},
    )
    source.fake = fake
    return source


def test_crawl_records_census(directory_source):
    result = crawl.crawl_source(directory_source)

    assert result.status == Crawl.Status.PARTIAL, "the stopped service makes it partial"
    assert result.datasets_seen == 3 and result.datasets_added == 3
    assert result.service_errors == 1

    parcels = Dataset.objects.get(url=f"{ROOT}/County/parcels/FeatureServer/0")
    assert parcels.jurisdiction.slug == "other-county", "folder mapping wins"
    assert parcels.service.type == "FeatureServer"
    assert parcels.feature_count == 5000
    assert parcels.name_fields == ["OWNER"]
    assert "Tax parcels" in parcels.search_text and "SALE_PR" in parcels.search_text
    assert {t.slug for t in parcels.topics.all()} >= {"parcels", "sales"}
    assert parcels.observations.count() == 1

    roads = Dataset.objects.get(url=f"{ROOT}/Basemap/MapServer/1")
    assert roads.jurisdiction.slug == "test-county", "no folder: the source default"

    stopped = Service.objects.get(name="County/old")
    assert "not started" in stopped.error


def test_partial_crawl_never_removes(directory_source):
    crawl.crawl_source(directory_source)
    # Parcels vanish, but the crawl is partial (a service is down), so nothing is removed
    directory_source.fake.routes[f"{ROOT}/County/parcels/FeatureServer/layers"] = {
        "layers": [],
        "tables": [],
    }
    directory_source.fake.routes[f"{ROOT}/County/parcels/MapServer/layers"] = {
        "layers": [],
        "tables": [],
    }
    second = crawl.crawl_source(directory_source)
    assert second.datasets_removed == 0
    assert Dataset.objects.filter(removed_at=None).count() == 3


def test_complete_crawl_marks_removed_and_returns(directory_source):
    routes = directory_source.fake.routes
    routes[f"{ROOT}/County"] = {
        "folders": [],
        "services": [{"name": "County/parcels", "type": "FeatureServer"}],
    }
    crawl.crawl_source(directory_source)

    routes[f"{ROOT}/County/parcels/FeatureServer/layers"] = {"layers": [], "tables": []}
    second = crawl.crawl_source(directory_source)
    assert second.status == Crawl.Status.SUCCEEDED
    assert second.datasets_removed == 2
    assert Dataset.objects.exclude(removed_at=None).count() == 2

    routes[f"{ROOT}/County/parcels/FeatureServer/layers"] = ROUTES[
        f"{ROOT}/County/parcels/FeatureServer/layers"
    ]
    crawl.crawl_source(directory_source)
    assert Dataset.objects.exclude(removed_at=None).count() == 0, "back again"


def test_schema_change_recorded(directory_source):
    crawl.crawl_source(directory_source)
    layer = ROUTES[f"{ROOT}/County/parcels/FeatureServer/layers"]["layers"][0]
    new_fields = [
        *layer["fields"],
        {"name": "ZONE", "type": "esriFieldTypeString", "alias": "Zone"},
    ]
    directory_source.fake.routes[f"{ROOT}/County/parcels/FeatureServer/layers"] = {
        "layers": [{**layer, "fields": new_fields}],
        "tables": [],
    }
    second = crawl.crawl_source(directory_source)
    assert second.datasets_changed == 1
    parcels = Dataset.objects.get(url=f"{ROOT}/County/parcels/FeatureServer/0")
    stored = [o.fields is not None for o in parcels.observations.order_by("observed_at")]
    assert stored == [True, True], "fields are stored only when the schema changes"


def test_manual_name_fields_survive_crawls(directory_source):
    crawl.crawl_source(directory_source)
    parcels = Dataset.objects.get(url=f"{ROOT}/County/parcels/FeatureServer/0")
    parcels.name_fields, parcels.name_fields_manual = ["PIN"], True
    parcels.save()
    crawl.crawl_source(directory_source)
    parcels.refresh_from_db()
    assert parcels.name_fields == ["PIN"]


def test_manual_topics_survive_rules(directory_source):
    crawl.crawl_source(directory_source)
    roads = Dataset.objects.get(url=f"{ROOT}/Basemap/MapServer/1")
    zoning = Topic.objects.get(slug="zoning")
    DatasetTopic.objects.create(dataset=roads, topic=zoning, assigned_by="manual")
    classify.apply_topics(roads)
    assert roads.topics.filter(slug="zoning").exists()


@pytest.mark.parametrize(
    ("name", "alias", "expected"),
    [
        ("CURRENT_OW", "Owner", True),
        ("Owner2", "Additional Owners", True),
        ("TAXPAYER_NAME", "", True),
        ("DEEDHOLD", "", True),
        ("OWNER_STRE", "", False),
        ("OWNER_CITY", "", False),
        ("OWNER_COUN", "", False),
        ("OwnerOccFlag", "", False),
        ("Ownership", "", False),
        ("NAME", "Road name", False),
    ],
)
def test_suggest_name_fields(name, alias, expected):
    fields = [{"name": name, "type": "esriFieldTypeString", "alias": alias}]
    assert bool(classify.suggest_name_fields(fields)) is expected


def make_dataset(source, service_name, service_type="MapServer", **kwargs):
    now = timezone.now()
    service, _ = Service.objects.get_or_create(
        url=f"{source.url}/{service_name}/{service_type}",
        defaults={
            "source": source,
            "name": service_name,
            "type": service_type,
            "first_seen": now,
            "last_seen": now,
        },
    )
    fields = kwargs.pop("fields", [{"name": "PIN", "type": "esriFieldTypeString", "alias": ""}])
    return Dataset.objects.create(
        source=source,
        service=service,
        url=f"{service.url}/{Dataset.objects.count()}",
        name=kwargs.pop("name", "Parcels"),
        geometry_type="esriGeometryPolygon",
        fields=fields,
        feature_count=kwargs.pop("feature_count", 100),
        jurisdiction=source.jurisdiction,
        first_seen=now,
        last_seen=now,
        **kwargs,
    )


def layer(service, *names, service_type="MapServer", count=100, kind="layer"):
    """A scanned layer; names default to a single PIN field."""
    names = names or ("PIN",)
    url = f"https://example.com/arcgis/rest/services/{service}/{service_type}/0"
    fields = [{"name": n, "type": "esriFieldTypeString", "alias": ""} for n in names]
    return directory.LayerInfo(
        url=url,
        service=service,
        service_type=service_type,
        layer_id=0,
        kind=kind,
        name="Parcels",
        geometry_type="esriGeometryPolygon" if kind == "layer" else "",
        count=count,
        fields=fields,
        signature=directory.field_signature(fields),
    )


def copies_of(layers, **kwargs):
    found = duplicates.find_copies(layers, {x.url: 1 for x in layers}, **kwargs)
    names = {x.url: x.service for x in layers}
    return {names[c]: names[o] for c, o in found.items()}


def test_copies_prefer_main_services():
    layers = [
        layer("County/AutoComplete"),
        layer("County/parcelswms", service_type="FeatureServer"),
        layer("County/parcels"),
        layer("County/other", count=99),
        layer("County/a", count=0),
        layer("County/b", count=0),
    ]
    assert copies_of(layers) == {
        "County/AutoComplete": "County/parcels",
        "County/parcelswms": "County/parcels",
    }, "different counts and empty layers aren't copies"


def test_copies_must_share_a_place():
    a, b = layer("A/parcels"), layer("B/parcels")
    assert duplicates.find_copies([a, b], {a.url: 1, b.url: 2}) == {}


def test_narrower_or_renamed_copies():
    wide = [f"F{i}" for i in range(40)]
    layers = [
        layer("County/AutoComplete", "OID_1", *wide),
        layer("County/parcels", "OID", *wide),
        layer("County/mapnet", *wide[:10]),
        layer("County/zoning", "ZONE", *wide[:3]),
        layer("County/labels", *wide[:3]),
    ]
    layers.append(layer("County/broken"))
    layers[-1].signature = frozenset()
    assert copies_of(layers) == {
        "County/AutoComplete": "County/parcels",
        "County/mapnet": "County/parcels",
    }, "a field the original lacks, too few shared fields, or no fields at all"


def test_watched_layers_are_never_copies():
    main, watched = layer("County/parcels"), layer("County/parcelswfs")
    assert copies_of([main, watched], keep={watched.url}) == {"County/parcels": "County/parcelswfs"}
    assert copies_of([main, watched], keep={main.url, watched.url}) == {}


def test_crawl_skips_and_deletes_copies(source, monkeypatch):
    main, wfs, auto = (
        layer("County/parcels"),
        layer("County/parcelswfs"),
        layer("County/AutoComplete"),
    )
    old = make_dataset(source, "County/parcelswfs")
    old.url = wfs.url
    old.save()
    scan = directory.DirectoryScan(root=source.url, layers=[main, wfs, auto])
    monkeypatch.setattr(crawl, "scan_source", lambda s: scan)

    result = crawl.crawl_source(source)

    assert list(Dataset.objects.values_list("url", flat=True)) == [main.url]
    assert result.datasets_seen == 1 and result.datasets_added == 1


def test_seed(county, tmp_path):
    seed_file = tmp_path / "sources.toml"
    seed_file.write_text(
        """
[[publishers]]
name = "Test Pub"
jurisdiction = "test-county"

[[sources]]
name = "Seeded"
slug = "seeded"
url = "https://seed.example.com/arcgis/rest/services/"
platform = "arcgis_server"
publisher = "Test Pub"
jurisdiction = "south-dakota"

[sources.folder_jurisdictions]
Test = "test-county"

[[watch]]
url = "https://seed.example.com/arcgis/rest/services/X/FeatureServer/0"
name_fields = ["OWNER"]
"""
    )
    counts = seed.load(seed_file)
    assert counts == {"publishers": 1, "sources": 1, "watched": 0, "watch_entries": 1}
    src = Source.objects.get(slug="seeded")
    assert src.url.endswith("/services"), "trailing slash stripped"
    assert src.publisher == Publisher.objects.get(name="Test Pub")
    assert src.jurisdiction_for("Test/svc") == county
    assert seed.load(seed_file) == counts, "idempotent"


def test_seed_rejects_unknown_jurisdiction(db, tmp_path):
    seed_file = tmp_path / "sources.toml"
    seed_file.write_text('[[publishers]]\nname = "X"\njurisdiction = "atlantis"\n')
    with pytest.raises(seed.SeedError, match="atlantis"):
        seed.load(seed_file)


def test_topics_ignore_service_names(source):
    roads = make_dataset(source, "County/parcels", name="Roads", feature_count=7)
    classify.apply_topics(roads)
    slugs = set(roads.topics.values_list("slug", flat=True))
    assert "parcels" not in slugs and "transportation" in slugs


@pytest.mark.parametrize(
    ("name", "field", "expected"),
    [
        ("Parcels", "ASSESSED_T", True),
        ("Parcels", "LAND_TV", True),
        ("Fences", "CONDITIONASSESSMENT", False),
        ("Trees", "Appraise", False),
        ("Water Main Risk Assessment", "RISK", False),
        ("Equalization Neighborhoods", "NBHD", True),
    ],
)
def test_assessed_values_rule(source, name, field, expected):
    d = make_dataset(
        source,
        "County/x",
        name=name,
        fields=[{"name": field, "type": "esriFieldTypeString", "alias": ""}],
    )
    classify.apply_topics(d)
    assert d.topics.filter(slug="assessed-values").exists() is expected


def test_reclassify_command(source):
    from django.core.management import call_command

    d = make_dataset(
        source,
        "County/x",
        name="Owners",
        fields=[{"name": "OWNER1", "type": "esriFieldTypeString", "alias": ""}],
    )
    call_command("reclassify")
    d.refresh_from_db()
    assert d.name_fields == ["OWNER1"]
    assert "OWNER1" in d.search_text
