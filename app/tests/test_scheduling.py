from datetime import timedelta

import pytest
from django.utils import timezone

from core import tasks
from core.models import Job


@pytest.fixture
def queued(monkeypatch):
    """Record enqueued jobs instead of sending them to Celery."""
    sent = []
    monkeypatch.setattr(
        tasks, "enqueue", lambda job, task: sent.append((job.kind, job.target_id)) or job
    )
    return sent


def test_is_due():
    now = timezone.now()
    assert tasks.is_due("0 5 * * *", None, now)
    assert not tasks.is_due("", None, now)
    assert not tasks.is_due("0 5 * * *", now, now)
    assert tasks.is_due("0 5 * * *", now - timedelta(days=2), now)
    assert not tasks.is_due("not a cron", now - timedelta(days=2), now)


def test_enqueue_due_work(dataset, queued):
    started = tasks.enqueue_due_work()
    assert started == {"crawls": 1, "harvests": 1}
    assert ("crawl", dataset.source_id) in queued
    assert ("harvest", dataset.pk) in queued

    # Scheduled just now, so nothing is due on the next tick
    queued.clear()
    assert tasks.enqueue_due_work() == {"crawls": 0, "harvests": 0}


def test_open_jobs_block_duplicates(dataset, queued):
    Job.objects.create(
        kind="harvest", label="x", target_type="census.dataset", target_id=dataset.pk
    )
    tasks.enqueue_due_work()
    assert ("harvest", dataset.pk) not in queued


def test_unwatched_datasets_are_not_harvested(dataset, queued):
    dataset.status = "discovered"
    dataset.save()
    tasks.enqueue_due_work()
    assert ("harvest", dataset.pk) not in queued


def test_harvest_task_records_job(dataset, settings):
    from core.jobs import create_job
    from harvest.tasks import harvest_dataset_task

    job = create_job("harvest", dataset)
    harvest_dataset_task(job.pk)
    job.refresh_from_db()
    assert job.status == Job.Status.SUCCEEDED
    assert job.result["added"] == 10


def test_failed_task_records_error(dataset, server):
    from core.jobs import create_job
    from harvest.tasks import harvest_dataset_task
    from harvest.validate import ValidationError

    server.reported_count = 1
    job = create_job("harvest", dataset)
    with pytest.raises(ValidationError):
        harvest_dataset_task(job.pk)
    job.refresh_from_db()
    assert job.status == Job.Status.FAILED
    assert "server reported 1" in job.error
