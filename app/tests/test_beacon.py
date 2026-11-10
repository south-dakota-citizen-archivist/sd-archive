import pytest
from django.urls import reverse

from census import beacon
from census.models import Source
from core import tasks
from core.models import Job
from sdgis.testing import PICKER


@pytest.fixture
def picker(tmp_path):
    path = tmp_path / "clients.html"
    path.write_text(PICKER)
    return path


def test_import_registers_sources(county, picker):
    result = beacon.load(picker)
    assert result == {"clients": 2, "created": 2, "unmatched": ["McCook County, SD"]}

    source = Source.objects.get(external_id="1225")
    assert source.platform == "beacon"
    assert source.slug == "test-county-beacon"
    assert source.jurisdiction == county
    assert source.publisher.jurisdiction == county
    assert source.crawl_schedule == ""
    assert not source.crawlable

    assert beacon.load(picker)["created"] == 0
    assert Source.objects.filter(platform="beacon").count() == 2


def test_beacon_sources_are_never_crawled(county, picker, client, monkeypatch):
    beacon.load(picker)
    source = Source.objects.get(external_id="1225")
    source.crawl_schedule = "* * * * *"  # even if someone sets one
    source.save()
    started = []
    monkeypatch.setattr(tasks, "start_crawl", started.append)
    tasks.enqueue_due_work()
    assert started == []

    response = client.post(reverse("census:source_crawl", args=[source.slug]), follow=True)
    assert b"can&#x27;t crawl" in response.content
    assert not Job.objects.exists()


def test_beacon_source_pages(county, picker, client):
    beacon.load(picker)
    page = client.get(reverse("census:source_detail", args=["test-county-beacon"]))
    assert page.status_code == 200
    assert b"Open in Schneider Beacon" in page.content
    assert b"Crawl now" not in page.content

    place = client.get(county.get_absolute_url())
    assert b"Test County (Beacon)" in place.content


def test_report_links(county, picker, dataset, capsys, monkeypatch):
    from django.core.management import call_command

    from census.management.commands import beacon_report_links as cmd
    from harvest.models import Feature, Harvest

    beacon.load(picker)
    harvest = Harvest.objects.create(dataset=dataset)
    for key, pin in [("1", " 12.34 "), ("2", "ROAD")]:
        Feature.objects.create(
            dataset=dataset,
            key=key,
            properties={"PIN": pin},
            content_hash=key,
            created_by=harvest,
            valid_from=harvest.started_at,
        )
    monkeypatch.setattr(
        cmd,
        "PARCEL_LAYERS",
        {
            "test-county-beacon": cmd.ParcelLayer(dataset.url, "PIN", None, confirmed=True),
            "mccook-county-beacon": cmd.ParcelLayer("x", "PIN", "Parcels", confirmed=False),
        },
    )
    call_command("beacon_report_links")
    rows = capsys.readouterr().out.strip().splitlines()
    assert rows == [
        "county,parcel_id,report_url",
        "Test County,12.34,https://beacon.schneidercorp.com/Application.aspx"
        "?App=TestCountySD&PageType=Report&KeyValue=12.34",
    ]


def test_source_list_groups_by_platform(county, picker, source, client):
    beacon.load(picker)
    page = client.get(reverse("census:source_list")).content.decode()
    arcgis, collapsed = page.index("ArcGIS Server"), page.index("<details")
    assert arcgis < collapsed < page.index("Test County (Beacon)"), "Beacon sits behind a click"
    assert page.index("Test server") < collapsed
