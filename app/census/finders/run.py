"""Run finders and record what they find."""

from __future__ import annotations

from . import arcgis_online, hosts, record

FINDERS = {"arcgis-online": arcgis_online, "hosts": hosts}


def run(names: list[str] | None = None) -> dict:
    """Each finder's numbers: what it read, and the candidates it added or saw again."""
    summary = {}
    for name in names or list(FINDERS):
        leads, stats = FINDERS[name].find()
        summary[name] = {**stats, **record(leads)}
    return summary


def finder_job(finders: list[str]):
    from core.models import Job

    return Job.objects.create(kind="find", label=f"Find GIS sources: {', '.join(finders)}",
                              args={"finders": finders})  # fmt: skip


def start(finders: list[str] | None = None):
    """Queue a finder run for the Celery worker."""
    from census.tasks import find_sources_task
    from core.jobs import enqueue

    return enqueue(finder_job(finders or list(FINDERS)), find_sources_task)
