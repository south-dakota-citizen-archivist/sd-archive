from celery import shared_task

from census.crawl import crawl_source
from census.models import Source
from core.jobs import run_job
from core.models import Job


def crawl_summary(crawl) -> dict:
    return {
        "crawl_id": crawl.pk,
        "status": crawl.status,
        "datasets": crawl.datasets_seen,
        "added": crawl.datasets_added,
        "changed": crawl.datasets_changed,
        "removed": crawl.datasets_removed,
        "errors": crawl.service_errors,
    }


@shared_task(name="census.tasks.crawl_source_task")
def crawl_source_task(job_id: int) -> dict:
    job = Job.objects.get(pk=job_id)
    source = Source.objects.get(pk=job.target_id)
    run_job(job, lambda: crawl_summary(crawl_source(source, job)))
    return job.result
