"""Public records: a record set's items, their files, and what's read from the files.

RecordSet → Record (one item at the source, as of the latest run) → Document (a file of a record)
→ Extraction (fields read from a document, with the quote for each, and review).
Direct-ingest rows are a Record's `data`; an Extraction is only what's read from a file.
"""

from django.db import models


class RecordSet(models.Model):
    """One kind of record from one source: private lobbyist registrations, executive orders.
    Several can make up one catalog dataset (public and private lobbyists → "lobbyists")."""

    class Ingest(models.TextChoices):
        ROWS = "rows", "Rows (the source lists the fields)"
        DOCUMENTS = "documents", "Documents (fields are read from files)"

    slug = models.SlugField(max_length=100, unique=True)
    name = models.CharField(max_length=255)
    dataset = models.CharField(max_length=100, db_index=True, help_text="catalog/datasets.toml id")
    description = models.TextField(blank=True)
    publisher = models.ForeignKey(
        "census.Publisher", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="recordsets",
    )  # fmt: skip
    base_url = models.URLField(max_length=1000, blank=True)
    ingest = models.CharField(max_length=20, choices=Ingest.choices, default=Ingest.ROWS)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Record(models.Model):
    """One item at the source, as of the latest run (no history: a change overwrites it)."""

    recordset = models.ForeignKey(RecordSet, on_delete=models.CASCADE, related_name="records")
    key = models.CharField(max_length=500, help_text="The source's own key for the item")
    data = models.JSONField(default=dict, blank=True, help_text="The fields, per the schema")
    content_hash = models.CharField(max_length=64)
    source_url = models.URLField(max_length=2000, blank=True)
    jurisdiction = models.ForeignKey(
        "places.Jurisdiction", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="records",
    )  # fmt: skip
    first_seen = models.DateTimeField(help_text="When a run first found it")
    updated_at = models.DateTimeField(auto_now=True)
    job = models.ForeignKey(
        "core.Job", null=True, blank=True, on_delete=models.SET_NULL, related_name="records"
    )
    legacy = models.JSONField(default=dict, blank=True, help_text="IDs in the system it came from")

    class Meta:
        ordering = ["recordset", "key"]
        constraints = [models.UniqueConstraint(fields=["recordset", "key"], name="record_key")]

    def __str__(self) -> str:
        return f"{self.recordset.slug}:{self.key}"


class Document(models.Model):
    """A file belonging to a record, stored once by its SHA-256 (media/blobs/)."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending (not downloaded)"
        STORED = "stored"
        READ = "read", "Read (text, form fields or OCR)"
        CATEGORIZED = "categorized"
        EXTRACTED = "extracted"
        FAILED = "failed"

    class Kind(models.TextChoices):
        FORM = "form", "Fillable form"
        NATIVE = "native", "Text layer"
        SCANNED = "scanned"
        MIXED = "mixed"
        OTHER = "other", "Not a PDF"
        UNKNOWN = "unknown", "Not typed yet"

    record = models.ForeignKey(Record, on_delete=models.CASCADE, related_name="documents")
    url = models.URLField(max_length=2000, blank=True, help_text="Where the file is published")
    sha256 = models.CharField(max_length=64, blank=True, db_index=True)
    path = models.CharField(max_length=1000, blank=True, help_text="The stored file, under media")
    filename = models.CharField(max_length=500, blank=True)
    mime_type = models.CharField(max_length=100, blank=True)
    size = models.BigIntegerField(null=True, blank=True)
    pages = models.PositiveIntegerField(null=True, blank=True)
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.UNKNOWN)
    category = models.CharField(max_length=100, blank=True, db_index=True)
    category_method = models.CharField(
        max_length=50, blank=True, help_text="rule, or the model that decided"
    )
    category_confidence = models.FloatField(null=True, blank=True)
    text = models.TextField(blank=True, help_text="For search")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    error = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    legacy = models.JSONField(default=dict, blank=True)

    def __str__(self) -> str:
        return self.filename or self.url or f"document {self.pk}"


class Extraction(models.Model):
    """Fields read from a document by a model, each with the quote it came from, and the
    review of them."""

    class Review(models.TextChoices):
        PENDING = "pending"
        APPROVED = "approved"
        REJECTED = "rejected"
        CORRECTED = "corrected", "Corrected by hand"

    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="extractions")
    schema = models.CharField(max_length=100, help_text="The extraction schema's name")
    schema_version = models.CharField(max_length=20, blank=True)
    data = models.JSONField(default=dict)
    evidence = models.JSONField(default=dict, blank=True, help_text="field → quote (and page)")
    unsupported = models.JSONField(
        default=list, blank=True, help_text="Fields whose quote isn't in the text"
    )
    model = models.CharField(max_length=100, blank=True)
    review = models.CharField(max_length=20, choices=Review.choices, default=Review.PENDING)
    reviewed_by = models.ForeignKey(
        "auth.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    legacy = models.JSONField(default=dict, blank=True)

    def __str__(self) -> str:
        return f"{self.schema} from {self.document}"
