"""Crawl a Source and fold what it publishes into the census."""

import logging

from django.db import transaction
from django.utils import timezone

from census import classify, duplicates
from census.models import Crawl, Dataset, DatasetObservation, Service, Source, Topic
from core.models import Job
from sdgis.arcgis import directory

logger = logging.getLogger(__name__)

ARCGIS_PLATFORMS = (Source.Platform.ARCGIS_SERVER, Source.Platform.ARCGIS_ONLINE)

# Metadata copied from a scan onto the Dataset; a change to any of these counts as "changed"
TRACKED = ("name", "description", "kind", "geometry_type", "fields_hash", "object_id_field")


def scan_source(source: Source) -> directory.DirectoryScan:
    if source.platform not in ARCGIS_PLATFORMS:
        raise NotImplementedError(f"no crawler for platform {source.platform!r} yet")
    return directory.scan(
        source.url,
        counts=source.crawl_counts,
        workers=source.max_workers,
        time_limit=source.time_limit_minutes * 60,
    )


def crawl_source(source: Source, job: Job | None = None) -> Crawl:
    crawl = Crawl.objects.create(source=source, job=job)
    try:
        result = scan_source(source)
        record_scan(source, crawl, result)
    except Exception as e:
        crawl.status = Crawl.Status.FAILED
        crawl.error = f"{type(e).__name__}: {e}"
        crawl.finished_at = timezone.now()
        crawl.save()
        raise
    return crawl


@transaction.atomic
def record_scan(source: Source, crawl: Crawl, scan: directory.DirectoryScan) -> Crawl:
    now = timezone.now()
    topics = list(Topic.objects.all())

    services = {}
    for info in scan.services:
        service, _ = Service.objects.update_or_create(
            url=info.url,
            defaults={
                "source": source,
                "name": info.name,
                "type": info.type,
                "folder": info.folder,
                "error": info.error,
                "last_seen": now,
                "removed_at": None,
            },
            create_defaults={
                "source": source,
                "name": info.name,
                "type": info.type,
                "folder": info.folder,
                "error": info.error,
                "first_seen": now,
                "last_seen": now,
            },
        )
        services[(info.name, info.type)] = service

    existing = {d.url: d for d in Dataset.objects.filter(source=source)}
    place_cache: dict[tuple[str, str], object] = {}

    def place_for(layer: directory.LayerInfo):
        """The dataset's place if someone set one, else the source's mapping."""
        if (d := existing.get(layer.url)) and d.jurisdiction_id:
            return d.jurisdiction
        key = (layer.folder, layer.service)
        if key not in place_cache:
            place_cache[key] = source.jurisdiction_for(layer.folder, layer.service)
        return place_cache[key]

    places = {layer.url: place_for(layer) for layer in scan.layers}
    harvested = set(
        Dataset.objects.filter(source=source, recordset__runs__isnull=False).values_list(
            "url", flat=True
        )
    )
    copies = duplicates.find_copies(
        scan.layers,
        {url: p.pk if p else None for url, p in places.items()},
        keep={url for url, d in existing.items() if d.is_watched} | harvested,
        age={url: d.pk for url, d in existing.items()},
    )
    added = changed = 0

    for layer in scan.layers:
        if layer.url in copies:
            continue
        dataset = existing.get(layer.url)
        is_new = dataset is None
        if is_new:
            dataset = Dataset(source=source, url=layer.url, first_seen=now)
        before = {k: getattr(dataset, k) for k in TRACKED} if not is_new else None

        dataset.service = services.get((layer.service, layer.service_type))
        dataset.layer_id = layer.layer_id
        dataset.name = layer.name[:500]
        dataset.description = layer.description
        dataset.kind = layer.kind
        dataset.geometry_type = layer.geometry_type
        dataset.fields = layer.fields
        dataset.fields_hash = classify.fields_hash(layer.fields)
        dataset.object_id_field = layer.object_id_field
        dataset.max_record_count = layer.max_record_count
        dataset.supports_pagination = layer.supports_pagination
        if layer.count is not None:
            dataset.feature_count = layer.count
        dataset.source_edited_at = layer.edited_at
        if dataset.jurisdiction_id is None:
            dataset.jurisdiction = places[layer.url]
        if not dataset.name_fields_manual:
            dataset.name_fields = classify.suggest_name_fields(layer.fields)
        dataset.search_text = classify.search_text(dataset)
        dataset.last_seen = now
        dataset.removed_at = None
        dataset.last_error = layer.error
        dataset.save()

        if is_new:
            added += 1
        elif before != {k: getattr(dataset, k) for k in TRACKED}:
            changed += 1

        classify.apply_topics(dataset, topics)
        record_observation(dataset, crawl, now, layer.count)

    # Only call datasets gone when the crawl read everything; a stopped service or a
    # timeout would otherwise look like mass deletions.
    removed = 0
    if scan.complete:
        seen = {layer.url for layer in scan.layers}
        gone = [d for url, d in existing.items() if url not in seen and d.removed_at is None]
        for d in gone:
            d.removed_at = now
        Dataset.objects.bulk_update(gone, ["removed_at"])
        removed = len(gone)
        Service.objects.filter(source=source, removed_at=None).exclude(
            url__in=[s.url for s in scan.services]
        ).update(removed_at=now)

    # Copies of other layers are noise; drop any recorded before they were spotted
    Dataset.objects.filter(source=source, url__in=copies).delete()

    errors = {s.url: s.error for s in scan.services if s.error} | scan.folder_errors
    crawl.status = Crawl.Status.SUCCEEDED if scan.complete else Crawl.Status.PARTIAL
    crawl.finished_at = timezone.now()
    crawl.services_seen = len(scan.services)
    crawl.service_errors = len(errors)
    crawl.datasets_seen = len(scan.layers) - len(copies)
    crawl.datasets_added = added
    crawl.datasets_changed = changed
    crawl.datasets_removed = removed
    crawl.errors = errors
    crawl.save()

    source.last_crawled_at = crawl.finished_at
    source.save(update_fields=["last_crawled_at"])
    logger.info(
        "%s: %d datasets (%d new, %d changed, %d removed), %d copies skipped, %d errors",
        source,
        crawl.datasets_seen,
        added,
        changed,
        removed,
        len(copies),
        len(errors),
    )
    return crawl


def record_observation(dataset: Dataset, crawl: Crawl, now, count: int | None) -> None:
    last = dataset.observations.order_by("-observed_at").first()
    DatasetObservation.objects.create(
        dataset=dataset,
        crawl=crawl,
        observed_at=now,
        feature_count=count,
        fields_hash=dataset.fields_hash,
        fields=dataset.fields if last is None or last.fields_hash != dataset.fields_hash else None,
    )
