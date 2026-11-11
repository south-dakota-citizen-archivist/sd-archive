"""Run a record set's source: fetch its items, check them, store the latest, log the run."""

from __future__ import annotations

import logging
from pathlib import Path

from django.db import transaction
from django.utils import timezone

from core.models import Job
from records import ingest, sources
from records.models import RecordSet, Run

logger = logging.getLogger(__name__)


def run_source(slug: str, *, job: Job | None = None, fetcher=None, force: bool = False) -> Run:
    """Fetch the record set's items and store them as one Run. A failure is logged on the
    run and raised; nothing it fetched is stored."""
    source = sources.get(slug)
    recordset = RecordSet.objects.get(slug=slug)
    run = Run.objects.create(recordset=recordset, job=job, forced=force)
    try:
        if source.items is None:
            raise ValueError(f"{slug}: the source has no items() to fetch")
        if fetcher is None:
            fetcher = raw_fetcher(slug)
        complete, stopped, own_checks = source.complete, None, {}
        try:
            found = source.items(fetcher)
            own_checks = getattr(found, "checks", None) or {}  # a source's own checks
            items = list(found)
        except sources.Stopped as stop:
            # the source stopped partway (a block, a daily budget): keep what it found, and
            # since it didn't see everything, mark nothing as gone
            items, complete, stopped = list(stop.items), False, stop.reason
        run.fetched_count = len(items)
        run.content_hash = ingest.run_hash(items)
        run.checks = dict(own_checks)
        if stopped:
            run.checks["stopped"] = stopped
        elif complete and not force:
            run.checks |= ingest.check_drop(
                recordset.records.current().count(), len(items), source.max_drop
            )
        elif complete:
            run.checks["drop"] = "skipped (forced)"
        with transaction.atomic():
            stored = ingest.store(run, items, complete=complete, covers=source.covers)
            if source.mentions and stored.written:
                _mentions(source, recordset, stored.written)
            for key, value in stored.counts().items():
                setattr(run, key, value)
            run.status = Run.Status.SUCCEEDED
            run.finished_at = timezone.now()
            run.save()
    except Exception as e:
        run.status = Run.Status.FAILED
        run.error = f"{type(e).__name__}: {e}"
        run.finished_at = timezone.now()
        run.save()
        raise
    logger.info("%s: %s", slug, stored.counts())
    return run


def raw_fetcher(slug: str):
    """A Fetcher whose stored responses go in media/raw/<slug>/ (kept, with a manifest), so a
    source can reuse what a stopped run already fetched."""
    from django.conf import settings

    from sdfetch import Fetcher

    return Fetcher(Path(settings.MEDIA_ROOT) / "raw" / slug)


def _mentions(source: sources.Source, recordset: RecordSet, keys: list[str]) -> None:
    """Rebuild the mentions of the records a run wrote."""
    from entities.mentions import replace

    records = recordset.records.filter(key__in=keys).select_related("recordset")
    found = [m for record in records for m in source.mentions(record)]
    replace(recordset.dataset, found, keys=[f"{recordset.slug}:{k}" for k in keys])
