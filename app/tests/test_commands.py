import pytest
from django.core.management import CommandError, call_command

from census.models import Crawl
from core.models import Job
from harvest import service
from harvest.frames import load_dataset
from harvest.models import Harvest
from sdgis.arcgis import client
from sdgis.testing import ROOT, FakeDirectory


def test_harvest_command(dataset):
    call_command("harvest", str(dataset.pk))
    assert Harvest.objects.get(dataset=dataset).added == 10
    assert Job.objects.get(kind="harvest").status == Job.Status.SUCCEEDED


def test_harvest_command_reports_failures(dataset, server):
    server.reported_count = 3
    with pytest.raises(CommandError, match="1 harvest"):
        call_command("harvest")


def test_harvest_command_unknown_id(db):
    with pytest.raises(CommandError, match="unknown"):
        call_command("harvest", "999")


def test_crawl_command(source, monkeypatch):
    monkeypatch.setattr(client, "make_session", lambda *a, **kw: FakeDirectory())
    source.url = ROOT
    source.save()
    call_command("crawl", source.slug)
    assert Crawl.objects.get(source=source).datasets_seen == 3
    with pytest.raises(CommandError, match="unknown source"):
        call_command("crawl", "nope")


def test_load_dataset_frames(dataset, server):
    first = service.harvest_dataset(dataset)
    server.owners[2] = "DOE, JANE"
    service.harvest_dataset(dataset)

    now = load_dataset(dataset.pk)
    assert list(now.columns[:3]) == ["OBJECTID", "NAME", "OWNER_NAM"], "field order kept"
    assert list(now["_key"][:3]) == ["1", "2", "3"], "numeric keys sort as numbers"
    assert now.crs.to_epsg() == 4326 and len(now) == 10
    assert now.set_index("_key").loc["2", "OWNER_NAM"] == "DOE, JANE"

    then = load_dataset(dataset.pk, as_of=first.started_at)
    assert then.set_index("_key").loc["2", "OWNER_NAM"] == "SMITH, JOHN 2"
    assert len(load_dataset(dataset.pk, history=True)) == 11


def test_build_readme(dataset, tmp_path):
    readme = tmp_path / "README.md"
    readme.write_text("# Hi\n<!-- sources-table:start -->\nold\n<!-- sources-table:end -->\nbye\n")
    call_command("build_readme", readme=readme)
    text = readme.read_text()
    assert "| [Test server](https://example.com/arcgis/rest/services) |" in text
    assert "old" not in text and text.endswith("bye\n")


def test_build_readme_skips_empty_database(db, tmp_path):
    readme = tmp_path / "README.md"
    original = "<!-- sources-table:start -->\nkeep\n<!-- sources-table:end -->\n"
    readme.write_text(original)
    call_command("build_readme", readme=readme)
    assert readme.read_text() == original
