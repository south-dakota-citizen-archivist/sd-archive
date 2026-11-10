from celery import shared_task

from census.models import Dataset
from core.jobs import run_job
from core.models import Job
from harvest.service import harvest_dataset


def harvest_summary(harvest) -> dict:
    return {
        "harvest_id": harvest.pk,
        "features": harvest.fetched_count,
        "added": harvest.added,
        "changed": harvest.changed,
        "removed": harvest.removed,
        "unchanged": harvest.unchanged,
    }


@shared_task(name="harvest.tasks.harvest_dataset_task")
def harvest_dataset_task(job_id: int) -> dict:
    job = Job.objects.get(pk=job_id)
    dataset = Dataset.objects.get(pk=job.target_id)
    force = bool(job.args.get("force"))
    run_job(job, lambda: harvest_summary(harvest_dataset(dataset, job, force=force)))
    return job.result
