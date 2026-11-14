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
    recordset = ensure_recordset(source)
    run = Run.objects.create(recordset=recordset, job=job, forced=force)
    try:
        if source.items is None:
            raise ValueError(f"{slug}: the source has no items() to fetch")
        if fetcher is None:
            fetcher = raw_fetcher(slug)
        complete, stopped, own_checks, covers = source.complete, None, {}, source.covers
        try:
            found = source.items(fetcher)
            own_checks = getattr(found, "checks", None) or {}  # a source's own checks
            covers = getattr(found, "covers", None) or covers  # what this run speaks for
            items = list(found)
        except sources.Stopped as stop:
            # the source stopped partway (a block, a daily budget): keep what it found, and
            # since it didn't see everything, mark nothing as gone
            items, complete, stopped = list(stop.items), False, stop.reason
            own_checks = stop.checks
        run.fetched_count = len(items)
        run.content_hash = ingest.run_hash(items)
        run.checks = dict(own_checks)
        if stopped:
            run.checks["stopped"] = stopped
        elif complete and not force:
            keys = recordset.records.current().values_list("key", flat=True).iterator()
            stored_keys = [k for k in keys if covers is None or covers(k)]  # the run speaks for
            found_keys = {i.key for i in items}
            gone = sum(1 for k in stored_keys if k not in found_keys)
            run.checks |= ingest.check_drop(len(stored_keys), len(items), source.max_drop, gone)
        elif complete:
            run.checks["drop"] = "skipped (forced)"
        with transaction.atomic():
            stored = ingest.store(run, items, complete=complete, covers=covers)
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


def ensure_recordset(source: sources.Source) -> RecordSet:
    """The source's record set; made from what the source declares if it's new."""
    try:
        return RecordSet.objects.get(slug=source.slug)
    except RecordSet.DoesNotExist:
        if not (source.name and source.dataset):
            raise
    from django.utils.text import slugify

    from census.models import Publisher

    # one transaction: the seed file is rewritten on commit, once the new publisher has its
    # record set (a publisher of record sets only isn't listed there)
    with transaction.atomic():
        publisher = None
        if source.publisher:
            publisher, _ = Publisher.objects.get_or_create(
                name=source.publisher, defaults={"slug": slugify(source.publisher)[:255]}
            )
        return RecordSet.objects.create(
            slug=source.slug, name=source.name, dataset=source.dataset, publisher=publisher,
            description=source.description, base_url=source.base_url, ingest=source.ingest,
        )  # fmt: skip


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
