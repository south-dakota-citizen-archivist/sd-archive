from django.contrib.gis.db import models


class Harvest(models.Model):
    """One download of a watched dataset's features."""

    class Status(models.TextChoices):
        RUNNING = "running"
        SUCCEEDED = "succeeded"
        SKIPPED = "skipped", "Skipped (unchanged at source)"
        FAILED = "failed"

    dataset = models.ForeignKey("census.Dataset", on_delete=models.CASCADE, related_name="harvests")
    job = models.ForeignKey("core.Job", null=True, blank=True, on_delete=models.SET_NULL)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.RUNNING)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    forced = models.BooleanField(default=False, help_text="Drop guard was bypassed")
    expected_count = models.PositiveIntegerField(null=True, blank=True)
    source_edited_at = models.DateTimeField(
        null=True, blank=True, help_text="The layer's last edit date, as the server reported it"
    )
    fetched_count = models.PositiveIntegerField(null=True, blank=True)
    added = models.PositiveIntegerField(default=0)
    changed = models.PositiveIntegerField(default=0)
    removed = models.PositiveIntegerField(default=0)
    unchanged = models.PositiveIntegerField(default=0)
    content_hash = models.CharField(max_length=64, blank=True)
    export = models.FileField(
        upload_to="exports/", blank=True, help_text="Set on the harvest that wrote the latest file"
    )
    checks = models.JSONField(default=dict, blank=True, help_text="Validation results")
    error = models.TextField(blank=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self) -> str:
        return f"Harvest of {self.dataset} at {self.started_at:%Y-%m-%d %H:%M}"

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.changed or self.removed)


class Feature(models.Model):
    """One record of a watched dataset, as of the latest harvest (no history: a change
    overwrites it, and a record gone from the source is deleted)."""

    dataset = models.ForeignKey("census.Dataset", on_delete=models.CASCADE, related_name="features")
    key = models.CharField(max_length=100, help_text="Source object ID, or a content hash")
    properties = models.JSONField()
    geometry = models.GeometryField(srid=4326, null=True, blank=True)
    content_hash = models.CharField(max_length=64)
    first_seen = models.DateTimeField(help_text="When a harvest first found it")
    harvest = models.ForeignKey(
        Harvest, null=True, blank=True, on_delete=models.SET_NULL, related_name="features",
        help_text="The harvest that last wrote it",
    )  # fmt: skip

    class Meta:
        ordering = ["dataset", "key"]
        constraints = [models.UniqueConstraint(fields=["dataset", "key"], name="feature_key")]

    def __str__(self) -> str:
        return f"{self.dataset_id}:{self.key}"
