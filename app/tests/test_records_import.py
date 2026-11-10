import json
from io import StringIO

import pytest
from django.core.management import call_command
from django.db import connection

from records.models import Document, Extraction, Record, RecordSet

# The liberator's tables, with only the columns the import reads
SCHEMA = """
CREATE TABLE scrapers_recordscustodian (id int PRIMARY KEY, name text, parent_id int);
CREATE TABLE scrapers_recordset (id int PRIMARY KEY, name text, description text,
  base_url text, custodian_id int);
CREATE TABLE scrapers_record (id int PRIMARY KEY, recordset_id int, source_url text,
  metadata jsonb, status text, created_at timestamptz DEFAULT now());
CREATE TABLE scrapers_document (id int PRIMARY KEY, record_id int, recordset_id int,
  file_url text, source_url text, file text, content_hash text, mime_type text,
  file_size bigint, page_count int, document_type text, document_category text, status text,
  error text, metadata jsonb, created_at timestamptz DEFAULT now());
CREATE TABLE scrapers_extractedrecord (id int PRIMARY KEY, recordset_id int, document_id int,
  source_url text, data jsonb, extraction_model text, reviewed_at timestamptz,
  review_status text, reviewer_notes text, raw_response text);
"""
DROP = """DROP TABLE IF EXISTS scrapers_recordscustodian, scrapers_recordset, scrapers_record,
  scrapers_document, scrapers_extractedrecord"""

ORDERS = "https://sdsos.gov/executive-orders/"
DOC = "https://sdsos.gov/Document.aspx?DocGuid={}"


@pytest.fixture
def prl(transactional_db):
    """A liberator database (in the test database, so the import's own connection sees it)."""
    with connection.cursor() as cur:
        cur.execute(DROP)
        cur.execute(SCHEMA)
        cur.execute("INSERT INTO scrapers_recordscustodian VALUES (1, 'South Dakota Secretary of "
                    "State', NULL), (2, 'Elections Division', 1)")  # fmt: skip
        cur.execute("INSERT INTO scrapers_recordset VALUES "
                    "(12, 'Executive Orders', '', '', 2), "
                    "(4, 'State Employee Salaries', '', '', 1), "
                    "(1, 'Newspaper Circulation Statements', '', '', 1)")  # fmt: skip

        def record(id_, rs, url, data):
            cur.execute("INSERT INTO scrapers_record (id, recordset_id, source_url, metadata, "
                        "status) VALUES (%s, %s, %s, %s, 'COMPLETE')",
                        [id_, rs, url, json.dumps(data)])  # fmt: skip

        def document(id_, record_id, rs, guid, **extra):
            cur.execute("INSERT INTO scrapers_document (id, record_id, recordset_id, file_url, "
                        "source_url, file, content_hash, document_type, document_category, "
                        "status, error, metadata) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, '', "
                        "'DOWNLOADED', '', %s)",
                        [id_, record_id, rs, DOC.format(guid), ORDERS, f"documents/{guid}.pdf",
                         f"{guid:0>64}", extra.get("type", "native_pdf"),
                         json.dumps(extra.get("meta", {}))])  # fmt: skip

        # every executive order shares the list page's URL, so documents key them
        record(1, 12, ORDERS, {"order_number": "2026-03"})
        record(2, 12, ORDERS, {"order_number": "2026-04"})
        document(10, 1, 12, "a1")
        document(11, 2, 12, "b2", type="image_pdf")
        # salaries have a URL each
        record(3, 4, "https://bfm.sd.gov/salaries/1", {"title": "Clerk", "salary": "$21.24"})
        # a statement with no record, only its record set
        document(12, None, 1, "c3", meta={"year": "1999", "newspaper": "Bison Courier"})
        cur.execute("INSERT INTO scrapers_extractedrecord VALUES "
                    "(1, 4, NULL, 'https://bfm.sd.gov/salaries/1', '{\"title\": \"Clerk\"}', '', "
                    "NULL, 'APPROVED', '', ''), "
                    "(2, NULL, 12, '', '{\"newspaper\": \"Bison Courier\"}', 'claude-x', now(), "
                    "'APPROVED', 'checked', '{}'), "
                    "(3, NULL, 11, '', '{\"last_name\": \"PO Box 196\"}', 'llama', now(), "
                    "'REJECTED', 'a PO box', '{}')")  # fmt: skip
    yield connection.settings_dict["NAME"]
    with connection.cursor() as cur:
        cur.execute(DROP)


def test_import(prl):
    call_command("import_prl", database=prl, stdout=StringIO())
    assert RecordSet.objects.get(slug="executive-orders").dataset == "executive-orders"
    orders = Record.objects.filter(recordset__slug="executive-orders").order_by("key")
    assert [r.key for r in orders] == [DOC.format("a1"), DOC.format("b2")]
    assert orders[0].legacy["prl_record_id"] == 1
    assert orders[0].recordset.publisher.parent.name == "South Dakota Secretary of State"

    salary = Record.objects.get(recordset__slug="state-employee-salaries")
    assert salary.key == "https://bfm.sd.gov/salaries/1" and salary.data["salary"] == "$21.24"

    statement = Record.objects.get(recordset__slug="newspaper-circulation")
    assert statement.data == {"year": "1999", "newspaper": "Bison Courier"}
    assert statement.documents.get().legacy["prl_file"] == "documents/c3.pdf"

    assert Document.objects.get(legacy__prl_document_id=11).kind == "scanned"
    assert Document.objects.get(legacy__prl_document_id=10).sha256 == "a1".rjust(64, "0")
    # the copy of a direct-ingest row isn't an extraction; the reviewed model output is
    assert sorted(Extraction.objects.values_list("review", flat=True)) == ["approved", "rejected"]
    assert Extraction.objects.get(review="rejected").review_notes == "a PO box"

    # again: the same rows, nothing doubled
    call_command("import_prl", database=prl, stdout=StringIO())
    assert Record.objects.count() == 4 and Document.objects.count() == 3
    assert Extraction.objects.count() == 2
