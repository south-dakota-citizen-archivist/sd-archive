"""The record pages: every record set listed, its overview, table and CSV, a run, a record
with its documents, places, names and links, and a document's file."""

import pytest
from django.urls import reverse


@pytest.fixture
def made(db, county, settings, tmp_path):
    from django.utils import timezone

    from entities.models import Mention
    from records.models import Document, Record, RecordLink, RecordSet, Run

    settings.MEDIA_ROOT = tmp_path
    sales = RecordSet.objects.create(slug="sales", name="Sales", dataset="property-sales",
                                     description="Sales reported for the ratio study.")  # fmt: skip
    gis = RecordSet.objects.create(slug="gis-data", name="GIS data", dataset="gis-data")
    run = Run.objects.create(recordset=sales, status="succeeded", fetched_count=3, added=3,
                             checks={"listed": 3})  # fmt: skip
    now = timezone.now()

    def record(rs, key, data, **kw):
        r = Record.objects.create(recordset=rs, key=key, data=data, content_hash=key,
                                  run=run if rs == sales else None, first_seen=now,
                                  **kw)  # fmt: skip
        r.places.set([county])
        return r

    big = record(sales, "2023:10", {"sale_price": 250000, "parcels": ["R1"], "county": "TEST"})
    record(sales, "2023:9", {"sale_price": 9000, "parcels": ["R2"], "county": "TEST"})
    record(sales, "2021:1", {"sale_price": 50, "parcels": ["R3"]}, dropped_at=now)
    parcel = record(gis, "App:1", {"key": "1", "sections": {"Summary": {"Quick Ref ID": "R1"}}})
    RecordLink.objects.create(from_record=big, to_record=parcel, kind="parcel",
                              from_field="parcels", to_field="Quick Ref ID",
                              value="R1")  # fmt: skip
    (tmp_path / "blobs").mkdir()
    (tmp_path / "blobs" / "a.pdf").write_bytes(b"%PDF-1.4 test")
    doc = Document.objects.create(record=big, url="https://example.gov/a.pdf", path="blobs/a.pdf",
                                  filename="a.pdf", mime_type="application/pdf",
                                  status="stored", text="the deed")  # fmt: skip
    Mention.objects.create(schema="Person", name="JANE DOE", normalized="jane doe",
                           dataset="property-sales", record_key="sales:2023:10",
                           field="buyer")  # fmt: skip
    return {"sales": sales, "big": big, "parcel": parcel, "doc": doc, "run": run}


def test_the_list(client, made):
    page = client.get(reverse("records:recordset_list")).content.decode()
    assert "Sales" in page and "GIS data" in page
    assert "2 record sets: 3 current records, 1 gone" in page


def test_a_record_sets_overview(client, made):
    page = client.get(reverse("records:recordset_detail", args=["sales"])).content.decode()
    assert "Sales reported for the ratio study." in page
    assert "sale_price" in page and "Stored: 1" in page
    assert reverse("records:run_detail", args=[made["run"].pk]) in page


def test_the_table(client, made):
    url = reverse("records:recordset_table", args=["sales"])
    page = client.get(url).content.decode()
    assert "2 records" in page and "2021:1" not in page  # current only
    assert "3 records" in client.get(url, {"show": "all"}).content.decode()
    # numbers sort as numbers
    page = client.get(url, {"sort": "sale_price", "dir": "desc"}).content.decode()
    assert page.index("2023:10") < page.index("2023:9")
    page = client.get(url, {"sort": "sale_price"}).content.decode()
    assert page.index("2023:9") < page.index("2023:10")
    assert "1 record." in client.get(url, {"q": "R2"}).content.decode()
    assert "1 record." in client.get(url, {"field": "parcels", "value": "r1"}).content.decode()
    # an htmx request gets just the rows
    rows = client.get(url, HTTP_HX_REQUEST="true").content.decode()
    assert "<form" not in rows and "2023:10" in rows


def test_the_csv(client, made):
    response = client.get(reverse("records:recordset_csv", args=["sales"]))
    lines = b"".join(response.streaming_content).decode().splitlines()
    assert lines[0] == "key,county,parcels,sale_price,source_url,first_seen"
    assert len(lines) == 3 and lines[1].startswith("2023:10,TEST,R1,250000,")


def test_a_record(client, made):
    page = client.get(made["big"].get_absolute_url()).content.decode()
    assert "250000" in page and "Test County" in page and "JANE DOE" in page
    assert reverse("records:document_file", args=[made["doc"].pk]) in page and "the deed" in page
    assert made["parcel"].get_absolute_url() in page  # its link to the parcel
    page = client.get(made["parcel"].get_absolute_url()).content.decode()
    assert made["big"].get_absolute_url() in page  # and back
    assert client.get("/records/sales/999999/").status_code == 404
    # a record's ID under another record set isn't found there
    assert client.get(f"/records/gis-data/{made['big'].pk}/").status_code == 404
    assert made["big"].get_absolute_url() == f"/records/sales/{made['big'].pk}/"


def test_a_documents_file(client, made):
    response = client.get(reverse("records:document_file", args=[made["doc"].pk]))
    assert response["Content-Type"] == "application/pdf"
    assert b"".join(response.streaming_content) == b"%PDF-1.4 test"


def test_a_run(client, made):
    page = client.get(reverse("records:run_detail", args=[made["run"].pk])).content.decode()
    assert "&quot;listed&quot;: 3" in page or '"listed": 3' in page
