"""Harvest a watched dataset: download, validate, store the latest records, export, index names."""

import logging
import shutil
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from census import classify
from census.models import Dataset
from core.models import Job
from harvest import export, store, validate
from harvest.models import Harvest
from sdgis.arcgis import client
from search import index

logger = logging.getLogger(__name__)

# Even when the server says nothing changed, download in full at least this often
FULL_HARVEST_EVERY = timedelta(days=7)


def staging_dir(dataset: Dataset) -> Path:
    """Where a harvest's download batches are saved as they arrive. A stopped harvest resumes
    from them; a finished one removes them."""
    return Path(settings.MEDIA_ROOT) / "staging" / "harvest" / str(dataset.pk)


def harvest_dataset(dataset: Dataset, job: Job | None = None, force: bool = False) -> Harvest:
    harvest = Harvest.objects.create(dataset=dataset, job=job, forced=force)
    try:
        # All or nothing: features, counts and the name index commit together. The download
        # itself is saved batch by batch in staging_dir() first, so it survives a stop.
        with transaction.atomic():
            run(dataset, harvest, force)
    except Exception as e:
        harvest.refresh_from_db()
        (Path(settings.MEDIA_ROOT) / export.export_relpath(harvest)).unlink(missing_ok=True)
        harvest.status = Harvest.Status.FAILED
        harvest.error = f"{type(e).__name__}: {e}"
        harvest.finished_at = timezone.now()
        harvest.save()
        dataset.last_error = harvest.error
        dataset.save(update_fields=["last_error"])
        raise
    export.promote(harvest)
    shutil.rmtree(staging_dir(dataset), ignore_errors=True)
    return harvest


def last_full_harvest_if_unchanged(dataset: Dataset, harvest: Harvest) -> Harvest | None:
    """The last full harvest, if the server's edit date and count both still match it and
    it's recent enough. Layers that don't report an edit date are always downloaded."""
    if harvest.source_edited_at is None:
        return None
    last = dataset.harvests.filter(status=Harvest.Status.SUCCEEDED).exclude(pk=harvest.pk).first()
    if (
        last is None
        or last.source_edited_at != harvest.source_edited_at
        or last.fetched_count != harvest.expected_count
        or timezone.now() - last.started_at > FULL_HARVEST_EVERY
    ):
        return None
    return last


def refresh_code_labels(dataset: Dataset, metadata: dict) -> None:
    """Take the layer's current code labels. A schema change is left for the next crawl,
    which records it."""
    fields = client.describe_fields(metadata)
    if fields != dataset.fields and classify.fields_hash(fields) == dataset.fields_hash:
        dataset.fields = fields
        dataset.save(update_fields=["fields"])


def skip(dataset: Dataset, harvest: Harvest, last: Harvest) -> None:
    harvest.status = Harvest.Status.SKIPPED
    harvest.unchanged = harvest.expected_count
    harvest.checks = {
        "skipped": f"edit date and count match harvest {last.pk}; next full download "
        f"by {last.started_at + FULL_HARVEST_EVERY:%Y-%m-%d}"
    }
    harvest.finished_at = timezone.now()
    harvest.save()
    # The archive matches the source as of now
    dataset.last_harvested_at = harvest.finished_at
    dataset.last_error = ""
    dataset.save(update_fields=["last_harvested_at", "source_edited_at", "last_error"])
    logger.info("%s: unchanged at source since %s, skipped", dataset, harvest.source_edited_at)


def run(dataset: Dataset, harvest: Harvest, force: bool) -> None:
    session = client.make_session()
    metadata = client.get_metadata(session, dataset.url)
    oid_field = client.object_id_field(metadata)
    refresh_code_labels(dataset, metadata)
    harvest.expected_count = client.get_count(session, dataset.url, dataset.where)
    harvest.source_edited_at = dataset.source_edited_at = client.last_edit(metadata)
    logger.info("%s: server reports %d features", dataset, harvest.expected_count)

    if not force and (last := last_full_harvest_if_unchanged(dataset, harvest)):
        skip(dataset, harvest, last)
        return

    df = client.fetch_features(
        session, dataset.url, metadata, dataset.where, dataset.batch_size,
        staging=staging_dir(dataset),
    )  # fmt: skip
    harvest.fetched_count = len(df)

    checks = validate.validate(df, harvest.expected_count, oid_field)
    if force:
        checks["drop"] = "skipped (forced)"
    else:
        checks |= validate.check_drop(store.current_count(dataset), len(df), dataset.max_drop)
    harvest.checks = checks

    if oid_field and oid_field in df.columns:
        df = df.sort_values(oid_field).reset_index(drop=True)

    rows = store.prepare(df, oid_field)
    harvest.content_hash = store.dataset_hash(rows)
    counts = store.apply(dataset, harvest, rows)
    for key, value in counts.items():
        setattr(harvest, key, value)

    # A new file only when something changed (or there's none yet)
    previous = dataset.harvests.exclude(pk=harvest.pk).exclude(export="").first()
    if harvest.has_changes or previous is None:
        harvest.export = export.write_parquet(df, harvest)

    harvest.status = Harvest.Status.SUCCEEDED
    harvest.finished_at = timezone.now()
    harvest.save()

    dataset.last_harvested_at = harvest.finished_at
    dataset.feature_count = harvest.fetched_count
    dataset.last_error = ""
    dataset.save(
        update_fields=["last_harvested_at", "feature_count", "source_edited_at", "last_error"]
    )

    if dataset.name_fields and (harvest.has_changes or not dataset.name_entries.exists()):
        index.rebuild(dataset)

    logger.info(
        "%s: %d added, %d changed, %d removed, %d unchanged",
        dataset,
        harvest.added,
        harvest.changed,
        harvest.removed,
        harvest.unchanged,
    )
