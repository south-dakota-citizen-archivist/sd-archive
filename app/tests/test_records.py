"""Record sets, their runs, and their documents' steps: fetch → read → categorize → extract →
review."""

import json
from datetime import timedelta
from io import StringIO
from pathlib import Path

import pytest
import requests
from django.core.management import CommandError, call_command
from django.db import IntegrityError
from django.utils import timezone
from pydantic import BaseModel

import sdllm
from census.models import Publisher
from records import documents, ingest, sources
from records.links import file_links
from records.models import Document, Extraction, Record, RecordSet, Run
from records.run import run_source
from records.sources import DocumentLink, Item, Source

# --- Helpers ---------------------------------------------------------------------------------


def text_pdf(*lines: str) -> bytes:
    """A one-page PDF with a text layer."""
    stream = "BT /F1 12 Tf 72 720 Td 14 TL " + " ".join(f"({x}) '" for x in lines) + " ET"
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        "/Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{body}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


ORDER = text_pdf("EXECUTIVE ORDER 2026-04",
                 "I, Larry Rhoden, Governor of the State of South Dakota, order flags lowered.",
                 "Dated March 3, 2026.")  # fmt: skip


class Resp:
    def __init__(self, content=ORDER, content_type="application/pdf", status_code=200,
                 headers=None):  # fmt: skip
        self.content, self.status_code = content, status_code
        self.headers = {"Content-Type": content_type, **(headers or {})}


class Fetcher:
    """Answers each URL from a dict: a Resp, or an exception to raise."""

    def __init__(self, answers=None):
        self.answers, self.calls = answers or {}, []

    def request(self, method, url, **kwargs):
        self.calls.append(url)
        answer = self.answers.get(url, Resp())
        if isinstance(answer, BaseException):
            raise answer
        return answer


class Order(BaseModel):
    number: str | None = None
    governor: str | None = None


class FakeModel:
    """An sdllm backend answering from a list, recording the model asked."""

    def __init__(self):
        self.answers, self.models = [], []

    def __call__(self, model, messages, schema, **params):
        self.models.append(model)
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer if isinstance(answer, str) else json.dumps(answer)


ORDER_ANSWER = {"data": {"number": "2026-04", "governor": "Larry Rhoden"},
                "evidence": [{"field": "number", "quote": "EXECUTIVE ORDER 2026-04"},
                             {"field": "governor", "quote": "Larry Rhoden"}]}  # fmt: skip


@pytest.fixture
def llm(monkeypatch, tmp_path):
    monkeypatch.setenv("SDLLM_CACHE", str(tmp_path / "llm-cache"))
    fake = FakeModel()
    sdllm.register_backend("fake", fake)
    return fake


@pytest.fixture(autouse=True)
def no_ocr(settings):
    settings.OCR_ENGINE = ""


@pytest.fixture
def rs(db):
    return RecordSet.objects.create(slug="orders", name="Executive orders",
                                    dataset="executive-orders", ingest="documents")  # fmt: skip


@pytest.fixture
def source(rs):
    src = sources.register(Source(
        slug="orders", model="fake/source",
        categories={"executive-order": "An executive order", "proclamation": "A proclamation",
                    "other": "Anything else"},
        schemas={"executive-order": Order},
    ))  # fmt: skip
    yield src
    sources.unregister("orders")


def new_run(recordset) -> Run:
    return Run.objects.create(recordset=recordset)


def make_record(recordset, key="1", **kwargs) -> Record:
    kwargs.setdefault("content_hash", "x")
    kwargs.setdefault("first_seen", timezone.now())
    return Record.objects.create(recordset=recordset, key=key, **kwargs)


def make_doc(record, url="https://sdsos.gov/eo/2026-04.pdf", **kwargs) -> Document:
    return Document.objects.create(record=record, url=url, **kwargs)


def stored_doc(rs, content=ORDER, **kwargs) -> Document:
    doc = make_doc(make_record(rs, key=kwargs.pop("key", "1")), **kwargs)
    documents.fetch(doc, Fetcher({doc.url: Resp(content)}))
    return doc


def age(obj, hours):
    type(obj).objects.filter(pk=obj.pk).update(updated_at=timezone.now() - timedelta(hours=hours))


# --- Models ----------------------------------------------------------------------------------


def test_record_key_is_unique_within_a_record_set(rs):
    make_record(rs, "A")
    other = RecordSet.objects.create(slug="b", name="B", dataset="b")
    make_record(other, "A")
    with pytest.raises(IntegrityError):
        make_record(rs, "A")


def test_document_url_is_unique_within_a_record(rs):
    first, second = make_record(rs, "1"), make_record(rs, "2")
    make_doc(first)
    make_doc(second)  # the same file on another record
    make_doc(first, url="")
    make_doc(first, url="")  # documents without a URL can repeat
    with pytest.raises(IntegrityError):
        make_doc(first)


@pytest.mark.parametrize(
    ("mime", "url", "pdf", "image"),
    [
        ("application/pdf", "https://x/x", True, False),
        ("", "https://x/doc.pdf", True, False),
        ("", "https://x/DOC.PDF?v=2", True, False),
        ("image/jpeg", "https://x/photo.jpg", False, True),
        ("image/png", "https://x/x", False, True),
        ("text/csv", "https://x/x.pdf", False, False),
    ],
)
def test_document_file_type(mime, url, pdf, image):
    doc = Document(mime_type=mime, url=url)
    assert (doc.is_pdf, doc.is_image) == (pdf, image)


def test_has_text_layer():
    assert Document(kind="native").has_text_layer and Document(kind="form").has_text_layer
    assert not Document(kind="scanned").has_text_layer


def test_publisher_parent_is_kept_optional(db):
    parent = Publisher.objects.create(name="Secretary of State", slug="sos")
    child = Publisher.objects.create(name="Elections", slug="elections", parent=parent)
    assert list(parent.children.all()) == [child]
    parent.delete()
    child.refresh_from_db()
    assert child.parent is None


def test_complete_records(rs):
    no_docs = make_record(rs, "none")
    done = make_record(rs, "done")
    make_doc(done, status="done")
    make_doc(done, url="https://x/2.pdf", status="done")
    mixed = make_record(rs, "mixed")
    make_doc(mixed, status="done")
    make_doc(mixed, url="https://x/2.pdf", status="extracted")
    unread = make_record(rs, "unread")
    make_doc(unread, status="stored")
    assert set(Record.objects.complete()) == {no_docs, done}
    assert set(Record.objects.incomplete()) == {mixed, unread}


def test_stuck_records(rs):
    failed = make_record(rs, "failed")
    make_doc(failed, status="failed")
    unreviewed = make_record(rs, "unreviewed")
    make_doc(unreviewed, status="extracted")
    make_doc(unreviewed, url="https://x/2.pdf", status="failed")
    one_done = make_record(rs, "one-done")
    make_doc(one_done, status="failed")
    make_doc(one_done, url="https://x/2.pdf", status="done")
    in_progress = make_record(rs, "in-progress")
    make_doc(in_progress, status="failed")
    make_doc(in_progress, url="https://x/2.pdf", status="read")
    no_docs = make_record(rs, "no-docs")
    recent = make_record(rs, "recent")
    make_doc(recent, status="failed")
    for r in (failed, unreviewed, one_done, in_progress, no_docs):
        age(r, 48)
        for d in r.documents.all():
            age(d, 48)
    age(recent, 48)  # its document changed just now
    assert set(Record.objects.stuck()) == {failed, unreviewed}
    assert not Record.objects.filter(key="recent").stuck().exists()


# --- Ingest ----------------------------------------------------------------------------------


def test_store_adds_changes_and_removes(rs):
    first = new_run(rs)
    out = ingest.store(first, [Item("a", {"name": "Alice"}, "https://x/a"),
                               Item("b", {"name": "Bob"})])  # fmt: skip
    assert out.counts() == {"added": 2, "changed": 0, "removed": 0, "unchanged": 0}
    a = Record.objects.get(key="a")
    assert (a.data, a.source_url, a.run, a.first_seen) == (
        {"name": "Alice"},
        "https://x/a",
        first,
        first.started_at,
    )
    assert Record.objects.get(key="b").source_url == ""

    second = new_run(rs)
    out = ingest.store(second, [Item("a", {"name": "Alice Updated"}, "https://x/a"),
                                Item("c", {"name": "Carol"})])  # fmt: skip
    assert out.counts() == {"added": 1, "changed": 1, "removed": 1, "unchanged": 0}
    assert (out.added, out.changed, out.removed) == (["c"], ["a"], ["b"])
    a.refresh_from_db()
    assert a.data == {"name": "Alice Updated"} and a.run == second
    assert a.first_seen == first.started_at  # kept
    assert set(rs.records.current().values_list("key", flat=True)) == {"a", "c"}
    b = Record.objects.get(key="b")  # gone from the source: kept, and marked
    assert b.dropped_at == second.started_at and b.data == {"name": "Bob"}
    assert list(rs.records.dropped()) == [b]


def test_a_dropped_record_stays_dropped_until_it_comes_back(rs):
    ingest.store(new_run(rs), [Item("a", {"v": 1}), Item("b", {"v": 1})])
    dropped_by = new_run(rs)
    ingest.store(dropped_by, [Item("a", {"v": 1})])
    out = ingest.store(new_run(rs), [Item("a", {"v": 1})])
    assert out.counts()["removed"] == 0  # already marked; not counted again
    assert Record.objects.get(key="b").dropped_at == dropped_by.started_at

    back = new_run(rs)
    out = ingest.store(back, [Item("a", {"v": 1}), Item("b", {"v": 1})])  # back, unchanged
    assert (out.changed, out.counts()["unchanged"]) == (["b"], 1)
    b = Record.objects.get(key="b")
    assert b.dropped_at is None and b.run == back


def test_drop_guard_counts_current_records_only(rs):
    ingest.store(new_run(rs), [Item(str(i), {}) for i in range(10)])
    ingest.store(new_run(rs), [Item(str(i), {}) for i in range(6)])  # 4 dropped
    assert rs.records.current().count() == 6
    assert ingest.check_drop(rs.records.current().count(), 5, 0.5) == {"drop": 0.1667}


def test_store_leaves_unchanged_items_alone(rs):
    first = new_run(rs)
    ingest.store(first, [Item("a", {"n": 1})])
    out = ingest.store(new_run(rs), [Item("a", {"n": 1})])
    assert out.counts()["unchanged"] == 1
    assert Record.objects.get(key="a").run == first


def test_partial_run_keeps_items_it_didnt_see(rs):
    ingest.store(new_run(rs), [Item("a", {}), Item("b", {})])
    out = ingest.store(new_run(rs), [Item("c", {})], complete=False)
    assert out.removed == []
    assert rs.records.count() == 3


def test_repeated_keys_collapse_to_the_last(rs):
    ingest.store(new_run(rs), [Item("a", {"v": 1}), Item("a", {"v": 2})])
    assert Record.objects.get(key="a").data == {"v": 2}


def test_documents_follow_their_item(rs):
    ingest.store(
        new_run(rs),
        [Item("a", {}, documents=["https://x/1.pdf", DocumentLink("https://x/2.pdf", "Two")])],
    )
    record = Record.objects.get(key="a")
    one = record.documents.get(url="https://x/1.pdf")
    assert one.status == "pending"
    assert record.documents.get(url="https://x/2.pdf").filename == "Two"
    Document.objects.filter(pk=one.pk).update(status="done")

    ingest.store(new_run(rs), [Item("a", {}, documents=["https://x/1.pdf", "https://x/3.pdf"])])
    two = record.documents.get(url="https://x/2.pdf")
    assert two.dropped_at is not None  # no longer listed: kept, marked
    assert set(record.documents.filter(dropped_at=None).values_list("url", "status")) == {
        ("https://x/1.pdf", "done"),
        ("https://x/3.pdf", "pending"),
    }  # kept, and added
    ingest.store(new_run(rs), [Item("b", {})])
    # the record left the source: it and its files are kept
    assert record.documents.count() == 3
    ingest.store(new_run(rs), [Item("a", {}, documents=["https://x/2.pdf"]), Item("b", {})])
    two.refresh_from_db()
    assert two.dropped_at is None  # listed again


def test_places_follow_their_item(rs, county, state):
    ingest.store(new_run(rs), [Item("a", {}, places=["0500000US46999", "0400000US46"]),
                               Item("b", {})])  # fmt: skip
    a = Record.objects.get(key="a")
    assert set(a.places.all()) == {county, state}
    assert list(county.records.all()) == [a]
    ingest.store(new_run(rs), [Item("a", {}, places=["0400000US46"]), Item("b", {})])
    assert list(a.places.all()) == [state]
    with pytest.raises(ValueError, match="unknown places"):
        ingest.store(new_run(rs), [Item("a", {}, places=["0500000US46000"])])


def test_check_drop():
    assert ingest.check_drop(None, 5, 0.5) == {"drop": None}
    assert ingest.check_drop(10, 6, 0.5) == {"drop": 0.4}
    with pytest.raises(ingest.DropTooLarge, match="fell from 10 to 4"):
        ingest.check_drop(10, 4, 0.5)


# --- Runs ------------------------------------------------------------------------------------


@pytest.fixture
def rows(rs):
    found = {"items": [Item("1", {"name": "Alice"}), Item("2", {"name": "Bob"})]}

    def items(fetcher):
        if isinstance(found["items"], Exception):
            raise found["items"]
        return iter(found["items"])

    src = sources.register(Source(slug="orders", items=items))
    yield found, src
    sources.unregister("orders")


def test_run_source_logs_the_run(rows):
    run = run_source("orders", fetcher=Fetcher())
    assert (run.status, run.fetched_count, run.added, run.removed) == ("succeeded", 2, 2, 0)
    assert run.finished_at and run.content_hash and run.checks == {"drop": None}
    again = run_source("orders", fetcher=Fetcher())
    assert (again.added, again.unchanged) == (0, 2)
    assert again.content_hash == run.content_hash


def test_failed_run_is_logged_and_raised(rows, rs):
    found, _ = rows
    found["items"] = ConnectionError("site down")
    with pytest.raises(ConnectionError):
        run_source("orders", fetcher=Fetcher())
    run = rs.runs.get()
    assert run.status == "failed" and "site down" in run.error and run.finished_at
    assert not rs.records.exists()


def test_run_refuses_a_large_drop_unless_forced(rows, rs):
    found, _ = rows
    run_source("orders", fetcher=Fetcher())
    found["items"] = []
    with pytest.raises(ingest.DropTooLarge):
        run_source("orders", fetcher=Fetcher())
    assert rs.records.count() == 2
    run = run_source("orders", fetcher=Fetcher(), force=True)
    assert run.removed == 2 and run.checks == {"drop": "skipped (forced)"}


def test_mentions_are_rebuilt_for_written_records(rows, rs):
    from entities.mentions import from_text
    from entities.models import Mention

    found, src = rows
    seen = []

    def mentions(record):
        seen.append(record.key)
        return from_text(record.data["name"], dataset=rs.dataset, record_key=str(record))

    src.mentions = mentions
    run_source("orders", fetcher=Fetcher())
    assert sorted(seen) == ["1", "2"]
    assert set(Mention.objects.values_list("record_key", "name")) == {
        ("orders:1", "Alice"),
        ("orders:2", "Bob"),
    }
    seen.clear()
    found["items"] = [Item("1", {"name": "Alice"}), Item("2", {"name": "Robert"})]
    run_source("orders", fetcher=Fetcher())
    assert seen == ["2"]  # the unchanged record isn't read again
    assert Mention.objects.get(record_key="orders:2").name == "Robert"


def test_a_source_without_items_fails(source):
    with pytest.raises(ValueError, match="no items"):
        run_source("orders", fetcher=Fetcher())


def test_adopt_rekeys_imported_records_and_merges_duplicates(rs):
    from records.compare import adopt

    old = make_record(
        rs,
        "https://x/2024/roby.pdf",
        data={"name": "Roby"},
        first_seen=timezone.now() - timedelta(days=9),
        legacy={"prl_record_id": 1},
    )
    make_doc(old, url="https://x/2024/roby.pdf")
    new = make_record(rs, "https://x/2025/roby.pdf", data={"name": "Roby"},
                      legacy={"prl_record_id": 2})  # fmt: skip
    make_doc(new, url="https://x/2025/roby.pdf")
    make_record(rs, "https://x/banks.pdf", data={"name": "Banks"})
    make_record(rs, "Roby-taken", data={"name": "Taken"})  # old key = another's new key
    new_keys = {"Roby": "Roby-taken", "Banks": "Banks", "Taken": "Taken"}
    sources.register(Source(slug="orders", legacy_key=lambda r: new_keys[r.data["name"]]))
    try:
        assert adopt("orders", dry_run=True).merged == {
            "Roby-taken": ["https://x/2024/roby.pdf", "https://x/2025/roby.pdf"]
        }
        assert rs.records.count() == 4
        out = adopt("orders")
    finally:
        sources.unregister("orders")
    assert out.rekeyed == 3
    assert set(rs.records.values_list("key", flat=True)) == {"Roby-taken", "Banks", "Taken"}
    roby = rs.records.get(key="Roby-taken")
    assert roby.pk == old.pk  # the first seen is kept
    assert set(roby.documents.values_list("url", flat=True)) == {
        "https://x/2024/roby.pdf",
        "https://x/2025/roby.pdf",
    }
    [merged] = roby.legacy["merged"]
    assert (merged["key"], merged["data"], merged["prl_record_id"]) == (
        "https://x/2025/roby.pdf",
        {"name": "Roby"},
        2,
    )
    assert merged["first_seen"] == new.first_seen.isoformat()


def test_adopt_only_before_the_first_run(rs):
    from records.compare import adopt

    sources.register(Source(slug="orders", legacy_key=lambda r: r.key))
    new_run(rs)
    try:
        with pytest.raises(ValueError, match="has runs"):
            adopt("orders")
    finally:
        sources.unregister("orders")


# --- Fetch -----------------------------------------------------------------------------------


def test_fetch_stores_the_file_by_content(rs, media_root):
    doc = stored_doc(rs)
    assert doc.status == "stored" and doc.error == ""
    assert doc.path.startswith("blobs/") and doc.path.endswith(f"{doc.sha256}.pdf")
    assert (Path(media_root) / doc.path).read_bytes() == ORDER
    assert (doc.mime_type, doc.size, doc.filename) == ("application/pdf", len(ORDER), "2026-04.pdf")
    assert (doc.kind, doc.pages) == ("native", 1)


def test_the_same_file_is_stored_once(rs, media_root):
    one = stored_doc(rs, key="1")
    two = stored_doc(rs, key="2", url="https://sdsos.gov/eo/copy.pdf")
    assert one.path == two.path and one.sha256 == two.sha256
    assert len(list((Path(media_root) / "blobs").rglob("*.pdf"))) == 1


def test_a_web_page_is_not_stored(rs, media_root):
    doc = stored_doc(rs, content=b"<html><body>Session expired</body></html>")
    assert doc.status == "failed" and "web page" in doc.error
    assert not doc.path and not (Path(media_root) / "blobs").exists()


def test_a_failed_request_fails_the_document(rs):
    doc = make_doc(make_record(rs))
    documents.fetch(doc, Fetcher({doc.url: requests.HTTPError("500 Server Error")}))
    doc.refresh_from_db()
    assert doc.status == "failed" and "500 Server Error" in doc.error


def test_setup_problems_stop_the_batch(rs):
    from sdfetch import OutsideHours

    doc = make_doc(make_record(rs))
    with pytest.raises(OutsideHours):
        documents.fetch(doc, Fetcher({doc.url: OutsideHours("outside the site's hours")}))
    doc.refresh_from_db()
    assert doc.status == "pending"


def png() -> bytes:
    import io

    from PIL import Image

    raw = io.BytesIO()
    Image.new("RGB", (40, 20), "white").save(raw, "PNG")
    return raw.getvalue()


def test_file_type_comes_from_the_bytes(rs):
    # sdcfr.sdsos.gov's Document.aspx serves the file itself (sdsos.gov's is a viewer)
    doc = make_doc(make_record(rs), url="https://sdcfr.sdsos.gov/Document.aspx?DocGuid=1")
    documents.fetch(
        doc,
        Fetcher(
            {
                doc.url: Resp(
                    ORDER,
                    "application/octet-stream",
                    headers={"Content-Disposition": 'attachment; filename="EO 2026-04.pdf"'},
                )
            }
        ),
    )
    assert (doc.mime_type, doc.status, doc.filename) == ("application/pdf", "stored",
                                                         "EO 2026-04.pdf")  # fmt: skip
    pic = make_doc(make_record(rs, "2"), url="https://x/scan")
    documents.fetch(pic, Fetcher({pic.url: Resp(png(), "application/octet-stream")}))
    assert (pic.mime_type, pic.kind, pic.path[-4:]) == ("image/png", "image", ".png")


def test_a_stored_file_isnt_fetched_again(rs):
    doc = stored_doc(rs)
    fetcher = Fetcher()
    documents.fetch(doc, fetcher)
    assert fetcher.calls == []


@pytest.mark.parametrize(
    ("mime", "url", "suffix"),
    [
        ("application/pdf", "", ".pdf"),
        ("image/jpeg", "", ".jpg"),
        ("image/png; charset=binary", "", ".png"),
        ("image/tiff", "", ".tif"),
        ("image/gif", "", ".gif"),
        ("image/webp", "", ".webp"),
        ("APPLICATION/PDF", "", ".pdf"),
        ("", "https://x/report.XLSX", ".xlsx"),
        ("", "https://x/doc.pdf?download=1", ".pdf"),
        ("application/octet-stream", "https://x/Document.aspx", ".aspx"),
        ("", "https://x/file", ".bin"),
    ],
)
def test_suffix_for(mime, url, suffix):
    assert documents.suffix_for(mime, url) == suffix


# --- Read ------------------------------------------------------------------------------------


def test_read_takes_the_text_layer(rs):
    doc = documents.read(stored_doc(rs))
    assert doc.status == "read" and "EXECUTIVE ORDER 2026-04" in doc.text
    assert doc.metadata["read"] == {"text": 1}


def test_read_needs_a_stored_file(rs):
    doc = documents.read(make_doc(make_record(rs)))
    assert doc.status == "failed" and "isn't stored" in doc.error


def test_read_finds_a_file_from_the_import(rs, tmp_path):
    (tmp_path / "prl" / "documents").mkdir(parents=True)
    (tmp_path / "prl" / "documents" / "eo.pdf").write_bytes(ORDER)
    doc = make_doc(
        make_record(rs),
        mime_type="application/pdf",
        status="stored",
        legacy={"prl_media": str(tmp_path / "prl"), "prl_file": "documents/eo.pdf"},
    )
    assert "Rhoden" in documents.read(doc).text


def test_read_turns_an_image_into_a_page(rs):
    doc = documents.read(stored_doc(rs, content=png(), url="https://x/scan.png"))
    assert doc.status == "read" and doc.pages == 1
    assert doc.metadata["read"] == {"empty": 1}  # no OCR in tests


# --- Categorize ------------------------------------------------------------------------------


def read_doc(rs, **kwargs) -> Document:
    return documents.read(stored_doc(rs, **kwargs))


def test_a_rule_decides_before_the_model(rs, source, llm):
    source.rule = lambda doc: "Executive Order" if "EXECUTIVE ORDER" in doc.text else None
    doc = documents.categorize(read_doc(rs), source)
    assert (doc.category, doc.category_method, doc.status) == (
        "executive-order",
        "rule",
        "categorized",
    )
    assert llm.models == []


def test_the_model_decides_otherwise(rs, source, llm):
    llm.answers.append({"label": "executive-order", "reason": "It says so."})
    doc = documents.categorize(read_doc(rs), source)
    assert (doc.category, doc.category_method, doc.status) == (
        "executive-order",
        "fake/source",
        "categorized",
    )
    assert doc.error == ""


def test_the_callers_model_wins(rs, source, llm):
    llm.answers.append({"label": "proclamation", "reason": "."})
    documents.categorize(read_doc(rs), source, model="fake/caller")
    assert llm.models == ["fake/caller"]


def test_a_category_with_nothing_to_extract_finishes_the_document(rs, source, llm):
    llm.answers.append({"label": "proclamation", "reason": "."})
    doc = documents.categorize(read_doc(rs), source)
    assert (doc.category, doc.status) == ("proclamation", "done")
    assert Record.objects.complete().filter(pk=doc.record_id).exists()


def test_an_unknown_category_is_other(rs, source):
    source.rule = lambda doc: "Budget Memo"
    assert documents.categorize(read_doc(rs), source).category == "other"


def test_one_category_needs_no_model(rs, llm):
    src = Source(slug="orders", categories={"executive-order": ""}, schemas={"": Order})
    doc = documents.categorize(read_doc(rs), src)
    assert (doc.category, doc.category_method, doc.status) == (
        "executive-order",
        "rule",
        "categorized",
    )
    assert llm.models == []


def test_categorize_failure_is_recorded(rs, source, llm):
    llm.answers.append(RuntimeError("model server crashed"))
    doc = documents.categorize(read_doc(rs), source)
    assert doc.status == "failed" and "model server crashed" in doc.error


def test_categorize_needs_a_stored_file(rs, source):
    doc = documents.categorize(make_doc(make_record(rs)), source)
    assert doc.status == "failed" and "isn't stored" in doc.error


# --- Extract and review ----------------------------------------------------------------------


def categorized_doc(rs, source) -> Document:
    source.rule = lambda doc: "executive-order"
    return documents.categorize(read_doc(rs), source)


def test_extract_reads_fields_with_quotes(rs, source, llm):
    llm.answers.append(ORDER_ANSWER)
    doc = categorized_doc(rs, source)
    ex = documents.extract(doc, source)
    assert ex.data == {"number": "2026-04", "governor": "Larry Rhoden"}
    assert ex.evidence == {"number": "EXECUTIVE ORDER 2026-04", "governor": "Larry Rhoden"}
    assert (ex.schema, ex.model, ex.review, ex.unsupported) == ("Order", "fake/source",
                                                               "pending", [])  # fmt: skip
    doc.refresh_from_db()
    assert doc.status == "extracted"


def test_a_field_without_its_quote_is_flagged(rs, source, llm):
    llm.answers.append({"data": {"number": "2026-05", "governor": "Larry Rhoden"},
                        "evidence": [{"field": "number", "quote": "EXECUTIVE ORDER 2026-05"},
                                     {"field": "governor", "quote": "Larry Rhoden"}]})  # fmt: skip
    assert documents.extract(categorized_doc(rs, source), source).unsupported == ["number"]


def test_a_new_reading_replaces_unreviewed_ones_but_not_reviewed(rs, source, llm):
    doc = categorized_doc(rs, source)
    approved = Extraction.objects.create(document=doc, schema="Order", data={"number": "old"},
                                         review="approved")  # fmt: skip
    stale = Extraction.objects.create(document=doc, schema="Order", data={"number": "stale"})
    llm.answers.append(ORDER_ANSWER)
    new = documents.extract(doc, source)
    assert set(doc.extractions.all()) == {approved, new}
    assert not Extraction.objects.filter(pk=stale.pk).exists()
    approved.refresh_from_db()
    assert approved.data == {"number": "old"}


def test_extract_model_choice(rs, source, llm):
    doc = categorized_doc(rs, source)
    llm.answers += [ORDER_ANSWER, ORDER_ANSWER]
    documents.extract(doc, source)
    documents.extract(doc, source, model="fake/caller")
    assert llm.models == ["fake/source", "fake/caller"]


def test_extract_failure_is_recorded(rs, source, llm):
    doc = categorized_doc(rs, source)
    llm.answers.append(RuntimeError("LLM error"))
    assert documents.extract(doc, source) is None
    doc.refresh_from_db()
    assert doc.status == "failed" and "LLM error" in doc.error


def test_extract_needs_text(rs, source, llm):
    doc = categorized_doc(rs, source)
    Document.objects.filter(pk=doc.pk).update(text="")
    doc.refresh_from_db()
    assert documents.extract(doc, source) is None
    assert doc.status == "failed" and "No text" in doc.error


def test_extract_without_a_schema_finishes_the_document(rs, source):
    doc = make_doc(make_record(rs), status="categorized", category="proclamation")
    assert documents.extract(doc, source) is None
    assert doc.status == "done"


def test_review(rs, source, llm, django_user_model):
    user = django_user_model.objects.create(username="editor")
    llm.answers += [ORDER_ANSWER, ORDER_ANSWER]
    doc = categorized_doc(rs, source)
    ex = documents.extract(doc, source)
    documents.review(ex, "rejected", user=user, notes="Wrong governor")
    doc.refresh_from_db()
    assert doc.status == "extracted" and ex.reviewed_by == user and ex.reviewed_at
    assert Record.objects.incomplete().filter(pk=doc.record_id).exists()

    ex = documents.extract(doc, source)
    with pytest.raises(ValueError):
        documents.review(ex, "corrected")
    documents.review(ex, "corrected", data={"number": "2026-04", "governor": "L. Rhoden"})
    doc.refresh_from_db()
    assert doc.status == "done" and ex.data["governor"] == "L. Rhoden"
    assert Record.objects.complete().filter(pk=doc.record_id).exists()


# --- Batches ---------------------------------------------------------------------------------


def test_process_moves_documents_through_every_step(rs, source, llm):
    source.rule = lambda doc: "executive-order"
    llm.answers += [ORDER_ANSWER, ORDER_ANSWER]
    record = make_record(rs)
    make_doc(record, url="https://x/1.pdf")
    make_doc(record, url="https://x/2.pdf")
    make_doc(record, url="https://x/broken.pdf")
    waiting = make_doc(make_record(rs, "2"), url="https://x/3.pdf", status="read")  # no file
    fetcher = Fetcher({"https://x/broken.pdf": requests.ConnectionError("reset")})
    counts = documents.process("orders", fetcher=fetcher)
    assert counts == {
        "fetch": {"done": 2, "failed": 1},
        "read": {"done": 2, "failed": 0},
        "categorize": {"done": 2, "failed": 1},
        "extract": {"done": 2, "failed": 0},
    }
    assert Extraction.objects.count() == 2
    waiting.refresh_from_db()
    assert waiting.status == "failed"


def test_process_steps_and_limit(rs, source):
    record = make_record(rs)
    for i in range(3):
        make_doc(record, url=f"https://x/{i}.pdf")
    counts = documents.process("orders", steps=("fetch",), limit=2, fetcher=Fetcher())
    assert counts == {"fetch": {"done": 2, "failed": 0}}
    assert Document.objects.filter(status="pending").count() == 1


# --- Links -----------------------------------------------------------------------------------


BASE = "https://sdsos.gov/elections-voting/"


@pytest.mark.parametrize(
    ("html", "urls"),
    [
        ('<a href="https://x.gov/a.pdf">A</a>', ["https://x.gov/a.pdf"]),
        ('<a href="files/a.pdf">A</a>', [BASE + "files/a.pdf"]),
        ('<a href="/assets/a.pdf">A</a>', ["https://sdsos.gov/assets/a.pdf"]),
        (
            '<a href="#a.pdf">A</a><a href="javascript:open(\'a.pdf\')">B</a>'
            '<a href="mailto:x@sd.gov?a.pdf">C</a>',
            [],
        ),
        ('<a href="page.html">A</a><a href="/about">B</a>', []),
        ('<a href="A.PDF">A</a>', [BASE + "A.PDF"]),
        ('<a href="a.pdf">A</a><a href="a.pdf">again</a>', [BASE + "a.pdf"]),
        ('<a href="a.pdf?v=2">A</a>', [BASE + "a.pdf?v=2"]),
        (
            '<a href="a.pdf">A</a><a href="b.xlsx">B</a><a href="c.tif">C</a>',
            [BASE + "a.pdf", BASE + "b.xlsx", BASE + "c.tif"],
        ),
        ("<p>No links</p>", []),
        ("", []),
        (None, []),
    ],
)
def test_file_links(html, urls):
    assert [link.url for link in file_links(html, BASE)] == urls


def test_file_links_text_extensions_and_scope():
    html = """<nav><a href="/menu.pdf">Menu</a></nav>
      <table id="results"><tr><td><a href="EO-2026-04.pdf"> Executive
        Order 2026-04 </a></td><td><a href="EO-2026-04.docx">Word</a></td></tr></table>"""
    links = file_links(html, BASE, within="#results")
    assert [(x.url, x.text) for x in links] == [
        (BASE + "EO-2026-04.pdf", "Executive Order 2026-04"),
        (BASE + "EO-2026-04.docx", "Word"),
    ]
    assert [x.url for x in file_links(html, BASE, extensions={".PDF"}, within="#results")] == [
        BASE + "EO-2026-04.pdf"
    ]
    assert len(file_links(html, BASE, within="#nothing")) == 3  # falls back to the page
    assert file_links('<a href="a.pdf">A</a>')[0].url == "a.pdf"  # no base: as written


def test_pages_decode_as_utf8_whatever_they_declare():
    from records.sources._tables import document

    page = '<meta charset="windows-1252"><p>IGLÚHAPI 6th\u00a0St.</p>'.encode()
    assert document(page).text_content() == "IGLÚHAPI 6th\u00a0St."
    old = '<meta charset="windows-1252"><p>\u201cquoted\u201d</p>'.encode("cp1252")
    assert document(old).text_content() == "\u201cquoted\u201d"


# --- Commands --------------------------------------------------------------------------------


def run_command(*args) -> str:
    out = StringIO()
    call_command(*args, stdout=out)
    return out.getvalue()


def test_retry_documents(rs):
    record = make_record(rs)
    never = make_doc(record, url="https://x/1.pdf", status="failed", error="500")
    unread = make_doc(record, url="https://x/2.pdf", status="failed", error="bad PDF",
                      path="blobs/aa/bb/x.pdf")  # fmt: skip
    model = make_doc(record, url="https://x/3.pdf", status="failed", error="timeout",
                     path="blobs/aa/bb/y.pdf", text="text", category_method="fake/x")  # fmt: skip
    ok = make_doc(record, url="https://x/4.pdf", status="pending")
    out = run_command("retry_documents", "--dry-run")
    assert "Would retry 3" in out and "→ pending" in out and "500" in out
    assert Document.objects.filter(status="failed").count() == 3

    other = RecordSet.objects.create(slug="other", name="Other", dataset="o")
    make_doc(make_record(other), status="failed")
    run_command("retry_documents", "--recordset", "ORDERS", "--limit", "2")
    statuses = {d.pk: (d.status, d.error) for d in Document.objects.all()}
    assert statuses[never.pk] == ("pending", "") and statuses[unread.pk] == ("stored", "")
    assert statuses[model.pk] == ("failed", "timeout") and statuses[ok.pk][0] == "pending"
    run_command("retry_documents", "--recordset", "orders")
    assert Document.objects.get(pk=model.pk).status == "categorized"
    assert Document.objects.filter(record__recordset=other, status="failed").exists()


def test_retry_documents_with_nothing_failed(db):
    assert "No failed documents" in run_command("retry_documents")


def test_scrape_command(rows, rs, monkeypatch):
    from core.models import Job

    monkeypatch.setattr("sdfetch.Fetcher", Fetcher)
    with pytest.raises(CommandError, match="unknown record set"):
        run_command("scrape", "nope")
    out = run_command("scrape", "orders")
    assert "'added': 2" in out
    job = Job.objects.get(kind="scrape")
    assert job.status == "succeeded" and job.target_type == "records.recordset"
    assert rs.runs.get().job == job


def test_documents_command(rs, source):
    with pytest.raises(CommandError, match="unknown step"):
        run_command("documents", "orders", "--steps", "fetch,ocr")
    with pytest.raises(CommandError, match="unknown record set"):
        run_command("documents", "nope")
    make_doc(make_record(rs), status="stored", path="blobs/missing.pdf")
    out = run_command("documents", "orders", "--steps", "read")
    assert "read" in out and "1 failed" in out


def test_status_lists_document_trouble(rs):
    record = make_record(rs)
    make_doc(record, status="failed")
    age(record, 48)
    Document.objects.update(updated_at=timezone.now() - timedelta(hours=48))
    ex_doc = make_doc(make_record(rs, "2"), url="https://x/2.pdf", status="extracted")
    Extraction.objects.create(document=ex_doc, schema="Order")
    out = run_command("status")
    assert "orders: 1 failed document(s)" in out
    assert "1 record(s) stuck" in out
    assert "1 reading(s) waiting on review" in out
