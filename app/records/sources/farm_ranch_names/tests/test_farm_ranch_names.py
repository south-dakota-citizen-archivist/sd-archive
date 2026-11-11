"""Farm, ranch and home name registrations: the parser against rows cut from the real page
(checked by hand), the source against a recording of the site, and a run into the database.
Re-record when the site changes: just record app/records/sources/farm_ranch_names"""

from pathlib import Path

import pytest

import sdfetch
import sdplaces
from entities.models import Mention
from records.models import Record, RecordSet
from records.run import run_source
from records.sources.farm_ranch_names import parse, source

FIXTURE = (Path(__file__).parent / "fixtures" / "farm-registrations.html").read_text()
DOCS = "https://sdsos.gov/Business-Services/trademark-name-registration/docs/farmranchdocs/"


def by_name(rows):
    out = {}
    for row in rows:
        out.setdefault(row.registration.name, []).append(row)
    return out


def test_parse_reads_every_row():
    rows = by_name(parse.rows(FIXTURE, source.URL))
    assert sum(len(v) for v in rows.values()) == 11

    [piersol] = rows["PIERSOL FARM (THE)"]
    reg = piersol.registration
    assert (reg.owners, reg.counties, reg.county_geoids) == (
        ["Lawrence L. Piersol"],
        ["Clay"],
        ["46027"],
    )
    assert (str(reg.date_registered), reg.date_registered_raw) == ("1975-01-17", "Jan 17, 1975")
    assert piersol.files == [(DOCS + "piersol-farm.PDF", "Certificate")]

    [black_hills] = rows["BLACK HILLS RANCH"]
    assert black_hills.registration.owners == [
        "Lester Clark",
        "George E. Clark",
        "David L. Clark",
        "C. C. Cattle Company",
    ]


def test_parse_quirks():
    rows = by_name(parse.rows(FIXTURE, source.URL))
    # the same name registered twice, years apart
    assert [str(r.registration.date_registered) for r in rows["BEAVER CREEK RANCH"]] == [
        "1984-03-05",
        "1989-01-12",
    ]
    # a note under the name, and an amendment next to the certificate
    [southgate] = rows["SOUTHGATE"]
    assert southgate.registration.notes == ["Amended 5/17/2010"]
    assert southgate.files == [(DOCS + "southgate-2005.PDF", "Certificate"),
                               (DOCS + "southgate-2010.PDF", "Amendment")]  # fmt: skip
    # a date typed with a period, and three counties
    [las] = rows["LAS ANGUS"]
    assert (str(las.registration.date_registered), las.registration.date_registered_raw) == (
        "2025-10-27",
        "Oct 27. 2025",
    )
    assert las.registration.county_geoids == ["46103", "46019", "46093"]
    # runs of spaces inside a cell
    [fink] = [v for k, v in rows.items() if k.startswith("BERT FINK")]
    assert fink[0].registration.owners == ["Bert Fink Jointly with J@T Investments, Unit 10"]


def test_county_in_force_on_the_date():
    assert parse.county_geoids(["Shannon"], parse.parse_date("May 1, 2010")) == ["46113"]
    with pytest.raises(sdplaces.UnknownPlace):
        parse.county_geoids(["Shannon"], parse.parse_date("May 1, 2016"))


def test_a_changed_layout_fails_loudly():
    with pytest.raises(parse.LayoutChanged, match="columns"):
        parse.rows(FIXTURE.replace("Owner(s)", "Owners"), source.URL)
    with pytest.raises(parse.LayoutChanged, match="cells"):
        parse.rows(FIXTURE.replace("<td>Clay</td>", "", 1), source.URL)


class FixtureFetcher:
    def request(self, method, url, **kwargs):
        assert url == source.URL

        class Resp:
            content = FIXTURE.encode()

        return Resp()


def test_run_stores_records_places_documents_and_mentions(db, sd_counties):
    RecordSet.objects.create(slug="farm-ranch-names", name="Farm names",
                             dataset="farm-ranch-names")  # fmt: skip
    run = run_source("farm-ranch-names", fetcher=FixtureFetcher())
    assert (run.status, run.added) == ("succeeded", 11)
    las = Record.objects.get(key="2025-10-27 LAS ANGUS")
    assert las.source_url == DOCS + "LASAngus.pdf"
    assert sorted(p.short_name for p in las.places.all()) == ["Butte", "Meade", "Pennington"]
    southgate = Record.objects.get(key="2005-05-31 SOUTHGATE")
    assert sorted(southgate.documents.values_list("metadata__label", flat=True)) == [
        "Amendment",
        "Certificate",
    ]
    assert set(Mention.objects.filter(record_key="farm-ranch-names:2025-10-27 LAS ANGUS")
               .values_list("name", "role", "schema")) == {
        ("Chase Sanftner", "owner", "Person"), ("Elaine Sanftner", "owner", "Person")}  # fmt: skip
    assert Mention.objects.filter(name="C. C. Cattle Company", schema="Company").exists()


@pytest.mark.vcr
def test_the_live_page(tmp_path):
    items = source.items(sdfetch.Fetcher(tmp_path, delay=0))
    assert len(items) >= 180
    assert len({i.key for i in items}) == len(items)
    assert all(i.documents and i.places and i.data["owners"] for i in items)
