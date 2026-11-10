"""Run work inside a Job record, whether it's a Celery task or a management command."""

import logging
import traceback
from collections.abc import Callable

from django.db import models
from django.utils import timezone

from core.models import Job

logger = logging.getLogger(__name__)


def target_label(obj: models.Model) -> tuple[str, int]:
    return f"{obj._meta.app_label}.{obj._meta.model_name}", obj.pk


def create_job(kind: str, target: models.Model, args: dict | None = None) -> Job:
    target_type, target_id = target_label(target)
    return Job.objects.create(
        kind=kind,
        label=str(target)[:500],
        target_type=target_type,
        target_id=target_id,
        args=args or {},
    )


def run_job(job: Job, fn: Callable[[], dict | None]) -> Job:
    """Call fn, recording status, timing, its returned dict, or the error.

    Exceptions are recorded and re-raised, so Celery and commands still see failures.
    """
    job.status = Job.Status.RUNNING
    job.started_at = timezone.now()
    job.save(update_fields=["status", "started_at"])
    try:
        result = fn() or {}
    except Exception as e:
        job.status = Job.Status.FAILED
        job.error = f"{type(e).__name__}: {e}\n\n{traceback.format_exc()}"
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "error", "finished_at"])
        logger.exception("Job %s failed", job)
        raise
    job.status = Job.Status.SUCCEEDED
    job.result = result
    job.finished_at = timezone.now()
    job.save(update_fields=["status", "result", "finished_at"])
    return job


def enqueue(job: Job, task) -> Job:
    """Hand a pending job to Celery. If no worker is running it stays pending."""
    async_result = task.delay(job.pk)
    job.celery_task_id = async_result.id or ""
    job.save(update_fields=["celery_task_id"])
    return job
