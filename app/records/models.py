"""Public records: a record set's items, their files, and what's read from the files.

RecordSet → Record (one item at the source, as of the latest run) → Document (a file of a record)
→ Extraction (fields read from a document, with the quote for each, and review). Each Run of a
record set's source is logged with its counts and checks.

A watched GIS layer is a record set too: its features are Records with a geometry, and each
harvest is a Run. Direct-ingest rows are a Record's `data`; an Extraction is only what's read
from a file.
"""

from datetime import timedelta

from django.contrib.gis.db import models
from django.db.models import Count, Q
from django.utils import timezone


class RecordSet(models.Model):
    """One kind of record from one source: private lobbyist registrations, executive orders.
    Several can make up one catalog dataset (public and private lobbyists → "lobbyists")."""

    class Ingest(models.TextChoices):
        ROWS = "rows", "Rows (the source lists the fields)"
        DOCUMENTS = "documents", "Documents (fields are read from files)"
        GIS = "gis", "A GIS layer (features with geometry)"

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
    gis_dataset = models.OneToOneField(
        "census.Dataset", null=True, blank=True, on_delete=models.CASCADE,
        related_name="recordset", help_text="For a GIS layer: the census's entry for it",
    )  # fmt: skip
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Run(models.Model):
    """One run of a record set's source: a GIS harvest, a scrape. Logs what it found and what
    it changed."""

    class Status(models.TextChoices):
        RUNNING = "running"
        SUCCEEDED = "succeeded"
        SKIPPED = "skipped", "Skipped (unchanged at source)"
        FAILED = "failed"

    recordset = models.ForeignKey(RecordSet, on_delete=models.CASCADE, related_name="runs")
    job = models.ForeignKey("core.Job", null=True, blank=True, on_delete=models.SET_NULL)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.RUNNING)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    forced = models.BooleanField(default=False, help_text="Drop guard was bypassed")
    expected_count = models.PositiveIntegerField(null=True, blank=True)
    source_edited_at = models.DateTimeField(
        null=True, blank=True, help_text="The source's last edit date, as it reported it"
    )
    fetched_count = models.PositiveIntegerField(null=True, blank=True)
    added = models.PositiveIntegerField(default=0)
    changed = models.PositiveIntegerField(default=0)
    removed = models.PositiveIntegerField(
        default=0, help_text="Gone from the source: marked dropped"
    )
    unchanged = models.PositiveIntegerField(default=0)
    content_hash = models.CharField(max_length=64, blank=True)
    export = models.FileField(
        upload_to="exports/", blank=True, help_text="Set on the run that wrote the latest file"
    )
    checks = models.JSONField(default=dict, blank=True, help_text="Validation results")
    error = models.TextField(blank=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self) -> str:
        return f"Run of {self.recordset} at {self.started_at:%Y-%m-%d %H:%M}"

    @property
    def dataset(self):
        """For a GIS layer: the census's Dataset."""
        return self.recordset.gis_dataset

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.changed or self.removed)


class RecordQuerySet(models.QuerySet):
    def current(self):
        """Records still at the source (not dropped)."""
        return self.filter(dropped_at=None)

    def dropped(self):
        return self.exclude(dropped_at=None)

    def complete(self):
        """Records with nothing left to read: no documents, or every document done."""
        return self.exclude(documents__status__in=Document.NOT_DONE)

    def incomplete(self):
        return self.filter(documents__status__in=Document.NOT_DONE).distinct()

    def stuck(self, hours: float = 24):
        """Records that can't finish without someone acting: every document failed or waits
        on a rejected or unreviewed reading, none is done, and nothing changed in `hours`."""
        cutoff = timezone.now() - timedelta(hours=hours)
        waiting = [Document.Status.FAILED, Document.Status.EXTRACTED]
        return (
            self.filter(updated_at__lt=cutoff)
            .annotate(
                n_docs=Count("documents", distinct=True),
                n_waiting=Count(
                    "documents", filter=Q(documents__status__in=waiting), distinct=True
                ),
            )
            .filter(n_docs__gt=0, n_waiting=models.F("n_docs"))
            .exclude(documents__updated_at__gte=cutoff)
        )


class Record(models.Model):
    """One item at the source, as of the latest run (no history: a change overwrites it). An
    item gone from the source is kept, marked with when it was first found gone (dropped_at).
    A GIS feature is a record with a geometry."""

    recordset = models.ForeignKey(RecordSet, on_delete=models.CASCADE, related_name="records")
    key = models.CharField(max_length=500, help_text="The source's own key for the item")
    data = models.JSONField(default=dict, blank=True, help_text="The fields, per the schema")
    geometry = models.GeometryField(srid=4326, null=True, blank=True)
    content_hash = models.CharField(max_length=64)
    source_url = models.URLField(max_length=2000, blank=True)
    places = models.ManyToManyField(
        "places.Jurisdiction", blank=True, related_name="records",
        help_text="The places it's in or about (several for a multi-county registration)",
    )  # fmt: skip
    first_seen = models.DateTimeField(help_text="When a run first found it")
    dropped_at = models.DateTimeField(
        null=True, blank=True, db_index=True,
        help_text="When a run first found it gone from the source; cleared if it comes back",
    )  # fmt: skip
    updated_at = models.DateTimeField(auto_now=True)
    run = models.ForeignKey(
        Run, null=True, blank=True, on_delete=models.SET_NULL, related_name="records",
        help_text="The run that last wrote it",
    )  # fmt: skip
    legacy = models.JSONField(default=dict, blank=True, help_text="IDs in the system it came from")

    objects = RecordQuerySet.as_manager()

    class Meta:
        ordering = ["recordset", "key"]
        constraints = [models.UniqueConstraint(fields=["recordset", "key"], name="record_key")]
        # counting a record set's current records reads this, not the table
        indexes = [models.Index(fields=["recordset"], condition=models.Q(dropped_at=None),
                                name="record_current_by_set")]  # fmt: skip

    def __str__(self) -> str:
        return f"{self.recordset.slug}:{self.key}"


class RecordLink(models.Model):
    """One record naming another by an ID both carry: a property sale and the GIS feature of
    a parcel it lists. Rebuilt in full by `manage.py link_records`; what matched is kept, so
    every link can be checked against both records."""

    from_record = models.ForeignKey(Record, on_delete=models.CASCADE, related_name="links_out")
    to_record = models.ForeignKey(Record, on_delete=models.CASCADE, related_name="links_in")
    kind = models.CharField(max_length=50, db_index=True, help_text="parcel")
    from_field = models.CharField(max_length=100, help_text="Where the ID is (parcels)")
    to_field = models.CharField(max_length=100, help_text="Where it matched (PARCELID)")
    value = models.CharField(max_length=200, help_text="The ID as the first record gives it")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["from_record", "to_record", "kind"],
                                               name="record_link")]  # fmt: skip

    def __str__(self) -> str:
        return f"{self.from_record} → {self.to_record} ({self.kind} {self.value})"


class Document(models.Model):
    """A file belonging to a record, stored once by its SHA-256 (media/blobs/)."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending (not downloaded)"
        STORED = "stored"
        READ = "read", "Read (text, form fields or OCR)"
        CATEGORIZED = "categorized"
        EXTRACTED = "extracted", "Extracted (waiting on review)"
        DONE = "done", "Done (reviewed, or nothing to extract)"
        FAILED = "failed"

    class Kind(models.TextChoices):
        FORM = "form", "Fillable form"
        NATIVE = "native", "Text layer"
        SCANNED = "scanned"
        MIXED = "mixed"
        IMAGE = "image", "Image"
        OTHER = "other", "Not a PDF or image"
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
    dropped_at = models.DateTimeField(
        null=True, blank=True,
        help_text="When its record stopped listing the file; the stored file is kept",
    )  # fmt: skip
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    legacy = models.JSONField(default=dict, blank=True)

    NOT_DONE = [s for s in Status.values if s != "done"]

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["record", "url"], condition=~Q(url=""), name="document_url"
            )
        ]

    def __str__(self) -> str:
        return self.filename or self.url or f"document {self.pk}"

    @property
    def is_pdf(self) -> bool:
        return self.mime_type == "application/pdf" or (
            not self.mime_type and self.url.lower().split("?")[0].endswith(".pdf")
        )

    @property
    def is_image(self) -> bool:
        return self.mime_type.startswith("image/")

    @property
    def has_text_layer(self) -> bool:
        return self.kind in (self.Kind.NATIVE, self.Kind.FORM, self.Kind.MIXED)


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
