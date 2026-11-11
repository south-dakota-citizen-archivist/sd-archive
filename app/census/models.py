import re

from django.contrib.gis.db import models
from django.urls import reverse
from django.utils.text import slugify

from places.models import Jurisdiction

DEFAULT_CRAWL_SCHEDULE = "0 3 * * 0"  # Sundays, 3 a.m.
DEFAULT_HARVEST_SCHEDULE = "0 5 * * *"  # daily, 5 a.m.


def name_keys(name: str) -> list[str]:
    """Normalized prefixes of a folder or service name, longest first, for matching it to a
    place: "BON_HOMME1_FSA_2021" -> ["bon_homme_fsa", "bon_homme", "bon"]."""
    words = [w.rstrip("0123456789") for w in re.split(r"[\W_]+", name.lower())]
    words = [w for w in words if w]
    return ["_".join(words[:n]) for n in range(len(words), 0, -1)]


class Publisher(models.Model):
    """The agency or office behind a source, like PRL's RecordsCustodian."""

    name = models.CharField(max_length=255, unique=True)
    slug = models.SlugField(max_length=255, unique=True)
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="children",
        help_text="The office above it (a division's department)",
    )  # fmt: skip
    jurisdiction = models.ForeignKey(
        Jurisdiction, null=True, blank=True, on_delete=models.SET_NULL, related_name="publishers"
    )
    website = models.URLField(blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        self.slug = self.slug or slugify(self.name)[:255]
        super().save(*args, **kwargs)


class Source(models.Model):
    """One place datasets are published: an ArcGIS services directory, an AGOL org, etc."""

    class Platform(models.TextChoices):
        ARCGIS_SERVER = "arcgis_server", "ArcGIS Server"
        ARCGIS_ONLINE = "arcgis_online", "ArcGIS Online"
        BEACON = "beacon", "Schneider Beacon"

    # Platforms the hub can list datasets from. Beacon is behind a bot challenge.
    CRAWLABLE = {Platform.ARCGIS_SERVER, Platform.ARCGIS_ONLINE}

    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True)
    url = models.URLField(max_length=1000, unique=True)
    platform = models.CharField(max_length=30, choices=Platform.choices)
    external_id = models.CharField(
        max_length=100, blank=True, help_text="The platform's own ID, e.g. a Beacon AppID"
    )
    publisher = models.ForeignKey(
        Publisher, null=True, blank=True, on_delete=models.SET_NULL, related_name="sources"
    )
    jurisdiction = models.ForeignKey(
        Jurisdiction,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="sources",
        help_text="Default jurisdiction for this source's datasets",
    )
    folder_jurisdictions = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            "For shared servers: top-level folder or start of a service name → jurisdiction"
            ' slug, e.g. {"Lawrence": "lawrence-county", "bon_homme": "bon-homme-county"}'
        ),
    )
    active = models.BooleanField(default=True)
    crawl_schedule = models.CharField(max_length=100, default=DEFAULT_CRAWL_SCHEDULE, blank=True)
    crawl_counts = models.BooleanField(default=True, help_text="Count features in every layer")
    max_workers = models.PositiveSmallIntegerField(
        default=2, help_text="Concurrent requests to this server"
    )
    time_limit_minutes = models.PositiveSmallIntegerField(default=30)
    found_via = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True)
    last_crawled_at = models.DateTimeField(null=True, blank=True)
    last_scheduled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    def get_absolute_url(self) -> str:
        return reverse("census:source_detail", args=[self.slug])

    @property
    def crawlable(self) -> bool:
        return self.platform in self.CRAWLABLE

    def jurisdiction_for(self, folder: str, service: str = "") -> "Jurisdiction | None":
        """The place a folder or service name maps to, else the source's own.

        The top-level folder is checked before the service name, and longer keys before
        shorter ones. Keys match case-insensitively, ignoring numbers on the ends of words.
        """
        mapping = {}
        for key, slug in self.folder_jurisdictions.items():
            if keys := name_keys(key):
                mapping[keys[0]] = slug
        for text in (folder.split("/")[0], service.split("/")[-1]):
            for key in name_keys(text):
                if key in mapping:
                    return Jurisdiction.objects.filter(slug=mapping[key]).first()
        return self.jurisdiction


class Crawl(models.Model):
    class Status(models.TextChoices):
        RUNNING = "running"
        SUCCEEDED = "succeeded"
        PARTIAL = "partial", "Partial (some services unreadable)"
        FAILED = "failed"

    source = models.ForeignKey(Source, on_delete=models.CASCADE, related_name="crawls")
    job = models.ForeignKey("core.Job", null=True, blank=True, on_delete=models.SET_NULL)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.RUNNING)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    services_seen = models.PositiveIntegerField(default=0)
    service_errors = models.PositiveIntegerField(default=0)
    datasets_seen = models.PositiveIntegerField(default=0)
    datasets_added = models.PositiveIntegerField(default=0)
    datasets_changed = models.PositiveIntegerField(default=0)
    datasets_removed = models.PositiveIntegerField(default=0)
    errors = models.JSONField(default=dict, blank=True, help_text="URL → error")
    error = models.TextField(blank=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self) -> str:
        return f"Crawl of {self.source} at {self.started_at:%Y-%m-%d %H:%M}"


class Service(models.Model):
    source = models.ForeignKey(Source, on_delete=models.CASCADE, related_name="services")
    name = models.CharField(max_length=500)
    type = models.CharField(max_length=50)
    url = models.URLField(max_length=1000, unique=True)
    folder = models.CharField(max_length=255, blank=True)
    error = models.TextField(blank=True)
    first_seen = models.DateTimeField()
    last_seen = models.DateTimeField()
    removed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["name", "type"]

    def __str__(self) -> str:
        return f"{self.name}/{self.type}"


class Topic(models.Model):
    """A subject tag. Rules match layer/service names and field names or labels."""

    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    name_pattern = models.CharField(
        max_length=500, blank=True, help_text="Case-insensitive regex on layer and service names"
    )
    field_pattern = models.CharField(
        max_length=500, blank=True, help_text="Case-insensitive regex on field names and labels"
    )
    order = models.PositiveSmallIntegerField(default=100)

    class Meta:
        ordering = ["order", "name"]

    def __str__(self) -> str:
        return self.name


class Dataset(models.Model):
    """The census unit: one layer or table."""

    class Kind(models.TextChoices):
        LAYER = "layer"
        TABLE = "table"

    class Status(models.TextChoices):
        DISCOVERED = "discovered"
        WATCHED = "watched", "Watched (harvested on a schedule)"
        IGNORED = "ignored"

    source = models.ForeignKey(Source, on_delete=models.CASCADE, related_name="datasets")
    service = models.ForeignKey(
        Service, null=True, blank=True, on_delete=models.SET_NULL, related_name="datasets"
    )
    url = models.URLField(max_length=1000, unique=True)
    layer_id = models.IntegerField(null=True, blank=True)
    name = models.CharField(max_length=500)
    description = models.TextField(blank=True)
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.LAYER)
    geometry_type = models.CharField(max_length=50, blank=True)
    fields = models.JSONField(
        default=list,
        blank=True,
        help_text='[{"name", "type", "alias"}], plus code labels (see client.describe_fields)',
    )
    fields_hash = models.CharField(max_length=64, blank=True)
    object_id_field = models.CharField(max_length=100, blank=True)
    max_record_count = models.PositiveIntegerField(null=True, blank=True)
    supports_pagination = models.BooleanField(null=True)
    feature_count = models.PositiveIntegerField(null=True, blank=True)
    source_edited_at = models.DateTimeField(
        null=True, blank=True, help_text="When the data last changed, if the server says"
    )
    jurisdiction = models.ForeignKey(
        Jurisdiction, null=True, blank=True, on_delete=models.SET_NULL, related_name="datasets"
    )
    topics = models.ManyToManyField(Topic, through="DatasetTopic", related_name="datasets")
    search_text = models.TextField(blank=True, help_text="Denormalized text for catalog search")
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.DISCOVERED, db_index=True
    )

    # Harvest settings (watched datasets)
    where = models.CharField(max_length=1000, default="1=1")
    batch_size = models.PositiveIntegerField(null=True, blank=True)
    max_drop = models.FloatField(
        default=0.1, help_text="Refuse a harvest if the feature count falls by more than this"
    )
    harvest_schedule = models.CharField(
        max_length=100, default=DEFAULT_HARVEST_SCHEDULE, blank=True
    )
    name_fields = models.JSONField(
        default=list, blank=True, help_text="Fields holding people/organization names"
    )
    name_fields_manual = models.BooleanField(
        default=False, help_text="Set by hand; crawls won't overwrite name_fields"
    )
    last_scheduled_at = models.DateTimeField(null=True, blank=True)
    last_harvested_at = models.DateTimeField(null=True, blank=True)

    first_seen = models.DateTimeField()
    last_seen = models.DateTimeField()
    removed_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)

    class Meta:
        ordering = ["source__name", "name"]
        indexes = [models.Index(fields=["source", "removed_at"])]

    @property
    def harvests(self):
        """This layer's harvests (its record set's runs), newest first."""
        from records.models import Run

        return Run.objects.filter(recordset__gis_dataset=self)

    @property
    def features(self):
        """This layer's harvested features (its record set's records)."""
        from records.models import Record

        return Record.objects.current().filter(recordset__gis_dataset=self)

    def __str__(self) -> str:
        return self.name

    def get_absolute_url(self) -> str:
        return reverse("census:dataset_detail", args=[self.pk])

    @property
    def is_watched(self) -> bool:
        return self.status == self.Status.WATCHED

    @property
    def is_spatial(self) -> bool:
        return self.kind == self.Kind.LAYER and bool(self.geometry_type)

    @property
    def field_names(self) -> list[str]:
        return [f["name"] for f in self.fields]


class DatasetTopic(models.Model):
    class AssignedBy(models.TextChoices):
        RULE = "rule"
        MANUAL = "manual"

    dataset = models.ForeignKey(Dataset, on_delete=models.CASCADE)
    topic = models.ForeignKey(Topic, on_delete=models.CASCADE)
    assigned_by = models.CharField(
        max_length=10, choices=AssignedBy.choices, default=AssignedBy.RULE
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["dataset", "topic"], name="unique_dataset_topic")
        ]


class DatasetObservation(models.Model):
    """What one crawl saw for one dataset: the count and schema history comes from these."""

    dataset = models.ForeignKey(Dataset, on_delete=models.CASCADE, related_name="observations")
    crawl = models.ForeignKey(Crawl, on_delete=models.CASCADE, related_name="observations")
    observed_at = models.DateTimeField()
    feature_count = models.PositiveIntegerField(null=True, blank=True)
    fields_hash = models.CharField(max_length=64)
    # Only stored when the schema differs from the previous observation
    fields = models.JSONField(null=True, blank=True)

    class Meta:
        ordering = ["-observed_at"]


class SourceCandidate(models.Model):
    """A possible new source found by a finder, waiting for review."""

    class Status(models.TextChoices):
        PENDING = "pending"
        ACCEPTED = "accepted"
        REJECTED = "rejected"

    url = models.URLField(max_length=1000, unique=True)
    title = models.CharField(max_length=500, blank=True)
    platform = models.CharField(max_length=30, choices=Source.Platform.choices, blank=True)
    found_via = models.CharField(max_length=255)
    evidence = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    source = models.ForeignKey(Source, null=True, blank=True, on_delete=models.SET_NULL)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.title or self.url
