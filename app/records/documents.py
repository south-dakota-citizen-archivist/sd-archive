"""A record's files, step by step: fetch → read → categorize → extract → review.

Each step moves a Document to its next status, or to FAILED with the error; a failed
document doesn't stop the others. Files are stored once by content (sdfetch's blob store,
`media/blobs/ab/cd/<sha256>.<ext>`), so the same file listed twice is kept once.

Problems with the setup rather than the file (outside a site's hours, a spent budget, low
disk, a blocked remote model) are raised, so a batch stops instead of failing every file.
"""

from __future__ import annotations

import logging
import tempfile
from collections.abc import Callable
from email.message import Message
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from records.models import Document, Extraction
from records.sources import Source

logger = logging.getLogger(__name__)

MAX_ERROR = 4000
# The most text sent to a model to decide a document's category
CATEGORIZE_CHARS = 8000
PAGE_READER_VERSION = 1

MIME_SUFFIX = {
    "application/pdf": ".pdf",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/tiff": ".tif",
    "image/gif": ".gif",
    "image/webp": ".webp",
}


def _setup_errors() -> tuple[type[BaseException], ...]:
    from sdfetch import BudgetSpent, DiskSpaceLow, NotNow
    from sdllm import RemoteCallBlocked

    return (NotNow, BudgetSpent, DiskSpaceLow, RemoteCallBlocked)


def _fail(doc: Document, error: str) -> Document:
    doc.status = Document.Status.FAILED
    doc.error = error[:MAX_ERROR]
    doc.save(update_fields=["status", "error", "updated_at"])
    logger.warning("document %s failed: %s", doc.pk, error.splitlines()[0] if error else "")
    return doc


# ---------------------------------------------------------------------------
# Files


def suffix_for(mime_type: str, url: str = "") -> str:
    """A file suffix from its MIME type, else from the URL's path (".bin" without one)."""
    mime = mime_type.split(";")[0].strip().lower()
    if mime in MIME_SUFFIX:
        return MIME_SUFFIX[mime]
    ext = PurePosixPath(urlsplit(url).path).suffix.lower()
    return ext if ext and len(ext) <= 6 else ".bin"


def filename_for(url: str, content_disposition: str = "") -> str:
    """The file's name: the server's (Content-Disposition), else the URL's last part."""
    if content_disposition:
        msg = Message()
        msg["content-disposition"] = content_disposition
        if name := msg.get_filename():
            return Path(name).name
    return unquote(PurePosixPath(urlsplit(url).path).name)


def sniff(content: bytes, declared: str) -> str:
    """The MIME type, trusting the bytes over a generic or wrong Content-Type."""
    declared = declared.split(";")[0].strip().lower()
    head = content[:16]
    if head.startswith(b"%PDF-"):
        return "application/pdf"
    for magic, mime in ((b"\xff\xd8\xff", "image/jpeg"), (b"\x89PNG", "image/png"),
                        (b"II*\x00", "image/tiff"), (b"MM\x00*", "image/tiff"),
                        (b"GIF8", "image/gif")):  # fmt: skip
        if head.startswith(magic):
            return mime
    if head[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    return declared or "application/octet-stream"


def is_web_page(content: bytes, mime: str) -> bool:
    """A web page where a file was expected: an error page, a login form, a viewer."""
    if mime in ("text/html", "application/xhtml+xml"):
        return True
    start = content[:512].lstrip().lower()
    return start.startswith((b"<!doctype html", b"<html"))


def file_path(doc: Document) -> Path | None:
    """Where the document's file is on disk: the blob store, or for a document imported
    before its file was moved there, the file in the system it came from."""
    if doc.path:
        return Path(settings.MEDIA_ROOT) / doc.path
    if (media := doc.legacy.get("prl_media")) and (name := doc.legacy.get("prl_file")):
        return Path(media) / name
    return None


def store():
    from sdfetch import Store

    return Store(settings.MEDIA_ROOT)


def describe_file(doc: Document, path: Path) -> None:
    """Set a stored file's page count and kind."""
    if doc.mime_type == "application/pdf":
        import pdfplumber

        import sddocs

        try:
            with pdfplumber.open(str(path)) as pdf:
                doc.pages = len(pdf.pages)
            kind = sddocs.kind(path)
        except Exception as e:  # noqa: BLE001 (a damaged PDF is a property of the file)
            doc.kind = Document.Kind.UNKNOWN
            doc.metadata = {**doc.metadata, "pdf_error": f"{type(e).__name__}: {e}"[:500]}
            return
        doc.kind = kind if kind in Document.Kind.values else Document.Kind.OTHER
    elif doc.mime_type.startswith("image/"):
        doc.kind, doc.pages = Document.Kind.IMAGE, 1
    else:
        doc.kind = Document.Kind.OTHER


def fetch(doc: Document, fetcher, *, refresh: bool = False) -> Document:
    """Download a document's file into the blob store."""
    if doc.path and not refresh:
        return doc
    if not doc.url:
        return _fail(doc, "No URL to fetch the file from")
    try:
        resp = fetcher.request("GET", doc.url, timeout=120)
        content = resp.content
    except _setup_errors():
        raise
    except Exception as e:  # noqa: BLE001 (recorded on the document)
        return _fail(doc, f"{type(e).__name__}: {e}")
    mime = sniff(content, resp.headers.get("Content-Type", ""))
    if not content:
        return _fail(doc, f"Empty response from {doc.url}")
    if is_web_page(content, mime):
        return _fail(doc, f"Got a web page ({mime}), not a file, from {doc.url}")
    doc.path = store().blob(content, doc.url, suffix=suffix_for(mime, doc.url),
                            status=getattr(resp, "status_code", None))  # fmt: skip
    doc.sha256 = PurePosixPath(doc.path).stem
    doc.mime_type = mime
    doc.size = len(content)
    doc.filename = doc.filename or filename_for(
        doc.url, resp.headers.get("Content-Disposition", "")
    )
    describe_file(doc, Path(settings.MEDIA_ROOT) / doc.path)
    doc.status = Document.Status.STORED
    doc.error = ""
    doc.save()
    return doc


# ---------------------------------------------------------------------------
# Reading


def _image_as_pdf(path: Path, out: Path) -> Path:
    from PIL import Image

    with Image.open(path) as img:
        frames = []
        for i in range(getattr(img, "n_frames", 1)):
            img.seek(i)
            frames.append(img.convert("RGB"))
    frames[0].save(out, "PDF", save_all=True, append_images=frames[1:], resolution=200)
    return out


def read(doc: Document, *, ocr_engine: str | None = None) -> Document:
    """Read a stored file's text: its form fields or text layer, else OCR (sddocs.pages)."""
    import sddocs

    path = file_path(doc)
    if path is None or not path.exists():
        return _fail(doc, "The file isn't stored (fetch it first)")
    engine = ocr_engine if ocr_engine is not None else settings.OCR_ENGINE
    cache = sddocs.PageCache(Path(settings.MEDIA_ROOT) / "pages")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            pdf = path
            if doc.is_image:
                pdf = _image_as_pdf(path, Path(tmp) / "image.pdf")
            elif not doc.is_pdf:
                return _fail(doc, f"Can't read a {doc.mime_type or 'file of unknown type'}")
            pages = list(sddocs.pages(pdf, ocr_engine=engine or None, cache=cache,
                                      version=PAGE_READER_VERSION))  # fmt: skip
    except _setup_errors():
        raise
    except Exception as e:  # noqa: BLE001
        return _fail(doc, f"{type(e).__name__}: {e}")
    doc.text = "\f".join(p["text"] for p in pages)
    doc.pages = len(pages)
    methods: dict[str, int] = {}
    for p in pages:
        methods[p["method"]] = methods.get(p["method"], 0) + 1
    doc.metadata = {**doc.metadata, "read": methods}
    doc.status = Document.Status.READ
    doc.error = ""
    doc.save()
    return doc


# ---------------------------------------------------------------------------
# Categorizing and extracting


def categorize(doc: Document, source: Source, *, model: str | None = None) -> Document:
    """Decide a document's category: the source's rule first, then the model. A category
    with nothing to extract finishes the document."""
    if file_path(doc) is None:
        return _fail(doc, "The file isn't stored (fetch it first)")
    names = list(source.categories)
    try:
        label = source.rule(doc) if source.rule else None
        method = "rule"
        if label is None and len(names) == 1:
            label = names[0]
        elif label is None and names:
            import sdllm

            answer = sdllm.classify(doc.text[:CATEGORIZE_CHARS], source.categories,
                                    model=model or source.model or None)  # fmt: skip
            label, method = answer.label, answer.model
    except _setup_errors():
        raise
    except Exception as e:  # noqa: BLE001
        return _fail(doc, f"{type(e).__name__}: {e}")
    label = slugify(label or "")
    if names and label not in names:
        label = "other"
    doc.category, doc.category_method = label, method if label or names else ""
    doc.status = Document.Status.CATEGORIZED if source.schema_for(label) else Document.Status.DONE
    doc.error = ""
    doc.save(update_fields=["category", "category_method", "status", "error", "updated_at"])
    return doc


def extract(doc: Document, source: Source, *, model: str | None = None) -> Extraction | None:
    """Read the category's fields from a document, each with its quote. A new reading
    replaces readings not yet reviewed; reviewed ones are never touched."""
    schema = source.schema_for(doc.category)
    if schema is None:
        doc.status = Document.Status.DONE
        doc.save(update_fields=["status", "updated_at"])
        return None
    if not doc.text.strip():
        _fail(doc, "No text to extract from (read it first, or it needs OCR)")
        return None
    import sdllm

    instructions = source.instructions.get(doc.category) or source.instructions.get("", "")
    try:
        result = sdllm.extract(schema, doc.text, model=model or source.model or None,
                               instructions=instructions)  # fmt: skip
    except _setup_errors():
        raise
    except Exception as e:  # noqa: BLE001
        _fail(doc, f"{type(e).__name__}: {e}")
        return None
    with transaction.atomic():
        doc.extractions.filter(review=Extraction.Review.PENDING).delete()
        extraction = Extraction.objects.create(
            document=doc,
            schema=schema.__name__,
            schema_version=str(getattr(schema, "VERSION", "")),
            data=result.data.model_dump(mode="json"),
            evidence=result.evidence,
            unsupported=result.unsupported,
            model=result.model,
        )
        doc.status = Document.Status.EXTRACTED
        doc.error = ""
        doc.save(update_fields=["status", "error", "updated_at"])
    return extraction


def review(extraction: Extraction, decision: str, *, user=None, notes: str = "",
           data: dict | None = None) -> Extraction:  # fmt: skip
    """Record a person's review. Approving (or correcting, with the right data) finishes the
    document; rejecting leaves it waiting for a new reading."""
    decision = Extraction.Review(decision)
    if decision == Extraction.Review.CORRECTED:
        if data is None:
            raise ValueError("a correction needs the corrected data")
        extraction.data = data
    extraction.review = decision
    extraction.reviewed_by = user
    extraction.reviewed_at = timezone.now()
    extraction.review_notes = notes
    with transaction.atomic():
        extraction.save()
        doc = extraction.document
        if decision in (Extraction.Review.APPROVED, Extraction.Review.CORRECTED):
            doc.status = Document.Status.DONE
            doc.save(update_fields=["status", "updated_at"])
    return extraction


# ---------------------------------------------------------------------------
# Batches


STEPS = ("fetch", "read", "categorize", "extract")
NEEDS = {
    "fetch": Document.Status.PENDING,
    "read": Document.Status.STORED,
    "categorize": Document.Status.READ,
    "extract": Document.Status.CATEGORIZED,
}


def process(slug: str, *, steps: tuple[str, ...] = STEPS, limit: int | None = None,
            model: str | None = None, fetcher=None,
            progress: Callable[[str, Document], None] | None = None) -> dict:  # fmt: skip
    """Move a record set's documents through the steps, each over the documents waiting on
    it (up to `limit` per step). Returns, per step, how many moved on and how many failed."""
    from records import sources

    source = sources.get(slug)
    counts: dict[str, dict[str, int]] = {}
    for step in STEPS:
        if step not in steps:
            continue
        waiting = Document.objects.filter(record__recordset__slug=slug, status=NEEDS[step])
        waiting = waiting.order_by("pk")[:limit] if limit else waiting.order_by("pk")
        tally = counts.setdefault(step, {"done": 0, "failed": 0})
        if step == "fetch" and fetcher is None:
            from sdfetch import Fetcher

            fetcher = Fetcher()
        for doc in waiting.iterator():
            if progress:
                progress(step, doc)
            if step == "fetch":
                fetch(doc, fetcher)
            elif step == "read":
                read(doc)
            elif step == "categorize":
                categorize(doc, source, model=model)
            else:
                extract(doc, source, model=model)
            tally["failed" if doc.status == Document.Status.FAILED else "done"] += 1
    return counts
