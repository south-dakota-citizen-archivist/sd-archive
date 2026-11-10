"""Celery Beat runs enqueue_due_work every minute; it starts crawls and harvests that are due."""

import logging
from datetime import datetime

from celery import shared_task
from croniter import croniter
from django.utils import timezone

from core.jobs import create_job, enqueue, mark_stalled, target_label
from core.models import Job

logger = logging.getLogger(__name__)


def is_due(cron: str, last: datetime | None, now: datetime) -> bool:
    if not cron:
        return False
    if last is None:
        return True
    try:
        next_run = croniter(cron, timezone.localtime(last)).get_next(datetime)
    except (ValueError, KeyError):
        logger.error("Invalid cron expression %r", cron)
        return False
    return next_run <= now


def has_open_job(obj) -> bool:
    target_type, target_id = target_label(obj)
    return Job.objects.filter(
        target_type=target_type,
        target_id=target_id,
        status__in=[Job.Status.PENDING, Job.Status.RUNNING],
    ).exists()


def start_crawl(source) -> Job:
    from census.tasks import crawl_source_task

    return enqueue(create_job("crawl", source), crawl_source_task)


def start_harvest(dataset, force: bool = False) -> Job:
    from harvest.tasks import harvest_dataset_task

    return enqueue(create_job("harvest", dataset, {"force": force}), harvest_dataset_task)


@shared_task(name="core.tasks.enqueue_due_work")
def enqueue_due_work() -> dict:
    from census.models import Dataset, Source

    now = timezone.now()
    started = {"crawls": 0, "harvests": 0}
    # Work whose worker died mid-run would otherwise count as open forever
    mark_stalled()

    sources = Source.objects.filter(active=True, platform__in=Source.CRAWLABLE)
    for source in sources.exclude(crawl_schedule=""):
        last = source.last_scheduled_at or source.last_crawled_at
        if is_due(source.crawl_schedule, last, now) and not has_open_job(source):
            source.last_scheduled_at = now
            source.save(update_fields=["last_scheduled_at"])
            start_crawl(source)
            started["crawls"] += 1

    watched = Dataset.objects.filter(status=Dataset.Status.WATCHED, removed_at=None).exclude(
        harvest_schedule=""
    )
    for dataset in watched:
        last = dataset.last_scheduled_at or dataset.last_harvested_at
        if is_due(dataset.harvest_schedule, last, now) and not has_open_job(dataset):
            dataset.last_scheduled_at = now
            dataset.save(update_fields=["last_scheduled_at"])
            start_harvest(dataset)
            started["harvests"] += 1

    if any(started.values()):
        logger.info("Started %s", started)
    return started
