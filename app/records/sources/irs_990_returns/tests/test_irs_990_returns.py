"""Form 990 returns: three real South Dakota returns (a 990, a 990-EZ and a 990-PF, checked by
hand) read, and a run that finds them in a year's index and reads them out of a zip file by
range requests, as it does from the IRS."""

import io
import zipfile
from pathlib import Path

from entities.models import Mention
from records.models import Record, RecordSet
from records.run import run_source
from records.sources._irs.remote_zip import open_zip
from records.sources.irs_990_returns import parse, source
from sdfetch import Store

FIXTURES = Path(__file__).parent / "fixtures"
F990 = (FIXTURES / "990-202410169349302601.xml").read_bytes()
F990EZ = (FIXTURES / "990EZ-202400229349200100.xml").read_bytes()
F990PF = (FIXTURES / "990PF-202410249349100001.xml").read_bytes()


def test_a_990():
    d = parse.parse(F990)
    assert (d["return_type"], d["ein"]) == ("990", "460431867")
    assert d["name"] == "Cornerstones Career Learning Center"
    assert (d["period_begin"], d["period_end"], d["tax_year"]) == ("2022-07-01", "2023-06-30",
                                                                   "2022")  # fmt: skip
    assert d["address"]["city"] == "Huron" and d["address"]["state"] == "SD"
    assert (d["total_revenue"], d["total_expenses"], d["total_assets_eoy"]) == (922287, 940406,
                                                                                373292)  # fmt: skip
    assert d["employees"] == 29
    assert d["signed_by"] == {"name": "Kimberly Olson", "title": "Executive Dir.",
                              "date": "2024-01-10"}  # fmt: skip
    assert d["preparer"] == {"firm": "Kinner & Company Ltd", "person": "Nathan Kinner"}
    assert len(d["people"]) == 15
    first = d["people"][0]
    assert (first["name"], first["title"], first["kind"]) == ("Kimberly Olson", "Executive Dir.",
                                                              "person")  # fmt: skip
    assert first["fields"]["ReportableCompFromOrgAmt"] == "68872"
    assert first["fields"]["AverageHoursPerWeekRt"] == "40.00"


def test_a_990ez_and_a_990pf():
    ez = parse.parse(F990EZ)
    assert ez["return_type"] == "990EZ" and ez["total_revenue"] == 34405
    assert ez["people"] == []  # the return lists "NONE"
    assert ez["signed_by"]["name"] == "NANCY LEACH"
    pf = parse.parse(F990PF)
    assert pf["return_type"] == "990PF" and pf["in_care_of"] == "% Derek Tuttle"
    assert [p["name"] for p in pf["people"]] == ["Derek Tuttle", "Kim Nelson",
                                                 pf["people"][2]["name"]]  # fmt: skip
    assert pf["people"][0]["fields"]["AverageHrsPerWkDevotedToPosRt"] == "2.00"


def make_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for oid, xml in (("202410169349302601", F990), ("202400229349200100", F990EZ),
                         ("202410249349100001", F990PF), ("202499999999999999", F990)):  # fmt: skip
            z.writestr(f"2024_TEOS_XML_01A/{oid}_public.xml", xml)
    return buf.getvalue()


ZIP = make_zip()
ZIP_URL = "https://apps.irs.gov/pub/epostcard/990/xml/2024/2024_TEOS_XML_01A.zip"
INDEX_URL = "https://apps.irs.gov/pub/epostcard/990/xml/2024/index_2024.csv"
PAGE = f'<a href="{INDEX_URL}">index</a><a href="{ZIP_URL}">zip</a>'
INDEX = (
    "RETURN_ID,FILING_TYPE,EIN,TAX_PERIOD,SUB_DATE,TAXPAYER_NAME,RETURN_TYPE,DLN,OBJECT_ID,"
    "XML_BATCH_ID\n"
    "1,EFILE,460431867,202306,2024,Cornerstones,990,1,202410169349302601,2024_TEOS_XML_01A\n"
    "2,EFILE,237137609,202308,2024,MITCHELL EDUCATION,990EZ,2,202400229349200100,x\n"
    "3,EFILE,871779895,202312,2024,Elk Point,990PF,3,202410249349100001,x\n"
    "4,EFILE,999999999,202312,2024,Not South Dakota,990,4,202499999999999999,x\n"
)
EO = "EIN,NAME\n460431867,A\n237137609,B\n871779895,C\n"


class Response:
    def __init__(self, content=b"", status=200, headers=None):
        self.content, self.status_code, self.headers = content, status, headers or {}
        self.text = content.decode("utf-8", "replace")


class FixtureFetcher:
    """The IRS, from fixtures: the downloads page, the South Dakota extract, a year's index,
    and a zip that answers range requests."""

    def __init__(self, store):
        self.store, self.ranges = store, 0

    def request(self, method, url, headers=None, **kwargs):
        if url == ZIP_URL and method == "HEAD":
            return Response(headers={"content-length": str(len(ZIP))})
        if url == ZIP_URL:
            start, end = map(int, headers["Range"].removeprefix("bytes=").split("-"))
            self.ranges += 1
            return Response(ZIP[start : end + 1], 206)
        return Response({parse.DOWNLOADS: PAGE.encode(), source.EO_SD: EO.encode(),
                         INDEX_URL: INDEX.encode()}[url])  # fmt: skip

    def fetch(self, url, name):
        if not self.store.has(name):
            self.store.save(name, self.request("GET", url).content, url)
        return self.store.read(name)


def test_reading_members_by_range():
    fetcher = FixtureFetcher(None)
    archive = open_zip(fetcher, ZIP_URL)
    assert len(archive.namelist()) == 4
    assert archive.read("2024_TEOS_XML_01A/202410169349302601_public.xml") == F990


def test_run(db, sd_places, tmp_path):
    RecordSet.objects.create(slug="irs-990-returns", name="990s", dataset="irs-990-returns")
    fetcher = FixtureFetcher(Store(tmp_path))
    run = run_source("irs-990-returns", fetcher=fetcher)
    assert run.added == 3 and run.checks["2024 returns"] == 3  # not the other state's
    ret = Record.objects.get(key="202410169349302601")
    assert ret.data["index"]["TAXPAYER_NAME"] == "Cornerstones"
    assert ret.source_url == f"{ZIP_URL}#2024_TEOS_XML_01A/202410169349302601_public.xml"
    assert ret.places.filter(short_name="Huron").exists()
    roles = set(Mention.objects.filter(record_key="irs-990-returns:202410169349302601")
                .values_list("role", flat=True))  # fmt: skip
    assert {"filer", "Executive Dir.", "Member", "paid preparer"} <= roles
    # again: the returns are kept, so the zip isn't read
    before = fetcher.ranges
    run = run_source("irs-990-returns", fetcher=fetcher)
    assert run.unchanged == 3 and fetcher.ranges == before
