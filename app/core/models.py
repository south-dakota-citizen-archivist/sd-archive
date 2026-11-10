from django.db import models
from django.utils import timezone


class Job(models.Model):
    """One run of a background task: a crawl, a harvest, a boundary load. PRL's ScrapeJob."""

    class Status(models.TextChoices):
        PENDING = "pending"
        RUNNING = "running"
        SUCCEEDED = "succeeded"
        FAILED = "failed"

    kind = models.CharField(max_length=50, db_index=True)
    label = models.CharField(max_length=500)
    # What the job acted on, e.g. ("census.source", 3), for linking from the jobs page
    target_type = models.CharField(max_length=50, blank=True)
    target_id = models.BigIntegerField(null=True, blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    celery_task_id = models.CharField(max_length=255, blank=True)
    args = models.JSONField(default=dict, blank=True)
    result = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.kind}: {self.label} ({self.status})"

    @property
    def seconds(self) -> float | None:
        if not self.started_at:
            return None
        return ((self.finished_at or timezone.now()) - self.started_at).total_seconds()
