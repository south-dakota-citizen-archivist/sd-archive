from django.contrib.gis.db import models
from django.db.models import Q


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
    export = models.FileField(upload_to="exports/", blank=True)
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
    """One version of one record. The current version has valid_to = NULL.

    A changed record keeps its old row (closed) and gets a new one, so history is
    a query: what was true at time T is valid_from <= T < valid_to.
    """

    dataset = models.ForeignKey("census.Dataset", on_delete=models.CASCADE, related_name="features")
    key = models.CharField(max_length=100, help_text="Source object ID, or a content hash")
    properties = models.JSONField()
    geometry = models.GeometryField(srid=4326, null=True, blank=True)
    content_hash = models.CharField(max_length=64)
    valid_from = models.DateTimeField()
    valid_to = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(Harvest, on_delete=models.CASCADE, related_name="created")
    closed_by = models.ForeignKey(
        Harvest, null=True, blank=True, on_delete=models.SET_NULL, related_name="closed"
    )

    class Meta:
        ordering = ["dataset", "key", "-valid_from"]
        indexes = [
            models.Index(fields=["dataset", "key", "valid_from"]),
            models.Index(
                fields=["dataset", "key"], condition=Q(valid_to=None), name="feature_current"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.dataset_id}:{self.key}"

    @property
    def is_current(self) -> bool:
        return self.valid_to is None
