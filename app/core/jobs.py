"""Run work inside a Job record, whether it's a Celery task or a management command."""

import logging
import os
import socket
import traceback
from collections.abc import Callable

from django.apps import apps
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
    job.worker = this_worker()
    job.save(update_fields=["status", "started_at", "worker"])
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


def this_worker() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # exists, owned by someone else
        return True
    return True


STALLED_NOTE = (
    "The process running this stopped partway (the laptop slept or shut down, or the worker "
    "crashed or restarted). It runs again when next due; a harvest reuses the batches it saved."
)


def mark_stalled() -> list[Job]:
    """Running jobs on this machine whose process is gone become STALLED, and the run rows
    they left open (a Harvest or Crawl still "running") are closed as failed, so the work can
    be scheduled again."""
    host = socket.gethostname()
    stalled = []
    for job in Job.objects.filter(status=Job.Status.RUNNING).exclude(worker=""):
        job_host, _, pid = job.worker.rpartition(":")
        if job_host != host or not pid.isdigit() or _alive(int(pid)):
            continue
        job.status = Job.Status.STALLED
        job.error = STALLED_NOTE
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "error", "finished_at"])
        stalled.append(job)
    if stalled:
        _close_runs(stalled)
        logger.warning("Marked %d stalled job(s): %s", len(stalled), [j.pk for j in stalled])
    return stalled


def _close_runs(jobs: list[Job]) -> None:
    """Close run rows (any model with a `job` link and a "running" status) left by these jobs."""
    for model in apps.get_models():
        fields = {f.name: f for f in model._meta.get_fields()}
        job_field, status = fields.get("job"), fields.get("status")
        if not job_field or getattr(job_field, "related_model", None) is not Job or not status:
            continue
        values = dict(getattr(status, "choices", None) or [])
        if "running" not in values or "failed" not in values:
            continue
        update = {"status": "failed"}
        names = fields.keys()
        if "error" in names:
            update["error"] = STALLED_NOTE
        if "finished_at" in names:
            update["finished_at"] = timezone.now()
        model.objects.filter(job__in=jobs, status="running").update(**update)
