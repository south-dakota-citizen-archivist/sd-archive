"""Legislators by session, from real stored API responses (2016 and 2026 members) and the
legislature data repository's file for the 2016 one."""

import shutil
from pathlib import Path

import pytest
from django.utils import timezone

from records.models import Record, RecordSet
from records.run import run_source
from records.sources._legislature import build
from records.sources.legislature_legislators_by_term import source

FIXTURES = Path(__file__).parent / "fixtures"
REPO_FILE = "data/legislators/sd-legislature-legislator-999.json"


@pytest.fixture
def raw(settings, tmp_path):
    """The crawl's store and the repository's files, as the fixtures."""
    settings.MEDIA_ROOT = tmp_path
    shutil.copytree(FIXTURES / "api", tmp_path / "raw" / "legislature" / "api")
    shutil.copytree(FIXTURES / "archive", tmp_path / "raw" / "legislature-archive")
    (tmp_path / "raw" / "legislature-archive" / "manifest.jsonl").write_text(
        f'{{"path": "{REPO_FILE}", "url": "https://github.com/x/blob/abc/{REPO_FILE}"}}\n'
    )
    from records.sources._legislature import match

    caches = (build._archive_urls, build.crosswalk, match.people)
    for c in caches:
        c.cache_clear()
    yield tmp_path
    for c in caches:
        c.cache_clear()


def test_the_repository_keeps_the_address_the_api_dropped(db, sd_places, raw):
    found = source.items()
    by_key = {i.key: i for i in found}
    assert set(by_key) == {"999", "4731"} and found.checks["without_detail"] == 0
    old = by_key["999"].data
    assert old["home_address1"] is None  # the API leaves it empty now
    repo_url = f"https://github.com/x/blob/abc/{REPO_FILE}"
    assert old["earlier"]["home_address1"] == {"value": "521 N. Prairie", "from": repo_url}
    assert old["session_year"] == 2016 and old["legislator_id"] == 3830
    assert by_key["999"].places[1] == "1600000US4659020"  # Sioux Falls, from the address
    assert len(by_key["999"].places) == 2  # no district: sdplaces has 2021's only
    new = by_key["4731"]
    assert new.places[-1] == "620L900US46010" and "earlier" not in new.data
    assert new.source_url == "https://sdlegislature.gov/Legislators/Profile/4731/Detail"


def test_imported_values_are_kept_and_carried_from_run_to_run(db, sd_all_places, raw):
    rs = RecordSet.objects.create(slug=source.SLUG, name="L", dataset="legislature")
    Record.objects.create(
        recordset=rs, key="https://sdlegislature.gov/Legislators/Profile/4731/2026",
        data={"legislator_url": "https://sdlegislature.gov/Legislators/Profile/4731/2026",
              "email": "Bobbi.Andera@sdlegislature.gov", "phone_capitol": "605-773-3851",
              "phone_home": "None", "legislator_profile_id": "4731"},
        legacy={"prl_record_id": 1}, first_seen=timezone.now(),
    )  # fmt: skip
    from records.compare import adopt

    adopt(source.SLUG)
    run_source(source.SLUG)
    kept = Record.objects.get(recordset=rs, key="4731").data["earlier"]
    assert kept["email"]["value"] == "Bobbi.Andera@sdlegislature.gov"
    assert kept["capitol_phone"]["from"].startswith("public-records-liberator, from ")
    assert "home_phone" not in kept  # "None" was nothing
    run_source(source.SLUG)  # the imported shape is gone now; the value stays
    assert Record.objects.get(recordset=rs, key="4731").data["earlier"] == kept
    from entities.models import Mention

    m = Mention.objects.get(record_key=f"{source.SLUG}:999")
    assert m.gold_entity_id == "sd-legislator:3830"
    assert m.properties["address"] == ["521 N. Prairie, Sioux Falls, SD 57104"]


def test_imported_records_keyed_by_profile_page():
    rec = type("R", (), {"key": "https://sdlegislature.gov/Legislators/Profile/4731/2026"})()
    assert source.legacy_key(rec) == "4731"
    assert source.from_legacy({"legislator_profile_id": "4731", "phone_home": "None",
                               "session_year": "2026"})["session_year"] == 2026  # fmt: skip
