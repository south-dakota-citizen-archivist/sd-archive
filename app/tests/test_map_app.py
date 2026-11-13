import pytest
from django.urls import reverse

from census import mapapps
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
    result = mapapps.load(picker)
    assert result == {"clients": 2, "created": 2, "unmatched": ["McCook County, SD"]}

    source = Source.objects.get(external_id="1225")
    assert source.platform == "map_app"
    assert source.slug == "test-county-map-app"
    assert source.jurisdiction == county
    assert source.publisher.jurisdiction == county
    assert source.crawl_schedule == ""
    assert not source.crawlable

    assert mapapps.load(picker)["created"] == 0
    assert Source.objects.filter(platform="map_app").count() == 2


def test_map_app_sources_are_never_crawled(county, picker, client, monkeypatch):
    mapapps.load(picker)
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


def test_map_app_source_pages(county, picker, client):
    mapapps.load(picker)
    page = client.get(reverse("census:source_detail", args=["test-county-map-app"]))
    assert page.status_code == 200
    assert b"Open in County map app" in page.content
    assert b"Crawl now" not in page.content

    place = client.get(county.get_absolute_url())
    assert b"Test County (map app)" in place.content


def test_report_links(county, picker, dataset, capsys, monkeypatch):
    from django.core.management import call_command

    from census.management.commands import map_app_report_links as cmd
    from records.gis import recordset_for
    from records.models import Record, Run

    mapapps.load(picker)
    harvest = Run.objects.create(recordset=recordset_for(dataset))
    for key, pin in [("1", " 12.34 "), ("2", "ROAD")]:
        Record.objects.create(
            recordset=harvest.recordset,
            key=key,
            data={"PIN": pin},
            content_hash=key,
            run=harvest,
            first_seen=harvest.started_at,
        )
    monkeypatch.setattr(
        cmd,
        "KEY_LAYERS",
        {
            "test-county-map-app": cmd.KeyLayer(dataset.url, "PIN", None, confirmed=True),
            "mccook-county-map-app": cmd.KeyLayer("x", "PIN", "Parcels", confirmed=False),
        },
    )
    call_command("map_app_report_links")
    rows = capsys.readouterr().out.strip().splitlines()
    assert rows == [
        "county,key,report_url",
        "Test County,12.34,https://beacon.schneidercorp.com/Application.aspx"
        "?App=TestCountySD&PageType=Report&KeyValue=12.34",
    ]


def test_source_list_groups_by_platform(county, picker, source, client):
    mapapps.load(picker)
    page = client.get(reverse("census:source_list")).content.decode()
    arcgis, collapsed = page.index("ArcGIS Server"), page.index("<details")
    assert arcgis < collapsed < page.index("Test County (map app)"), "map apps sit behind a click"
    assert page.index("Test server") < collapsed
