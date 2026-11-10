import json

from django.contrib import messages
from django.contrib.gis.db.models.functions import AsGeoJSON
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Case, Count, Q, When
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from shapely.geometry import mapping

from census import sources as new_sources
from census.models import (
    Crawl,
    Dataset,
    DatasetObservation,
    Publisher,
    Source,
    Topic,
)
from core.tasks import start_crawl, start_harvest
from places.models import Jurisdiction
from sdgis.arcgis import client

PAGE_SIZE = 50


def filtered_datasets(params) -> tuple:
    qs = Dataset.objects.select_related("source", "jurisdiction", "service").prefetch_related(
        "topics"
    )
    filters = {
        "q": params.get("q", "").strip(),
        "place": params.get("place", ""),
        "topic": params.get("topic", ""),
        "source": params.get("source", ""),
        "kind": params.get("kind", ""),
        "status": params.get("status", ""),
        "removed": params.get("removed", ""),
    }
    ordering = ["source__name", "name"]
    if filters["q"]:
        for word in filters["q"].split():
            qs = qs.filter(search_text__icontains=word)
        # Datasets named for the search come before ones that only mention it in a field
        qs = qs.annotate(name_hit=Case(When(name__icontains=filters["q"], then=0), default=1))
        ordering = ["name_hit", *ordering]
    if filters["place"]:
        qs = qs.filter(jurisdiction__slug=filters["place"])
    if filters["topic"]:
        qs = qs.filter(topics__slug=filters["topic"])
    if filters["source"]:
        qs = qs.filter(source__slug=filters["source"])
    if filters["kind"]:
        qs = qs.filter(kind=filters["kind"])
    if filters["status"]:
        qs = qs.filter(status=filters["status"])
    if filters["removed"] != "1":
        qs = qs.filter(removed_at=None)
    return qs.distinct().order_by(*ordering), filters


def dataset_list(request: HttpRequest) -> HttpResponse:
    qs, filters = filtered_datasets(request.GET)
    page = Paginator(qs, PAGE_SIZE).get_page(request.GET.get("page"))
    context = {"page": page, "filters": filters, "total": page.paginator.count}
    if request.headers.get("HX-Request"):
        return render(request, "census/_dataset_results.html", context)
    context |= {
        "topics": Topic.objects.all(),
        "sources": Source.objects.all(),
        "places": Jurisdiction.objects.filter(datasets__isnull=False)
        .distinct()
        .order_by("kind", "name"),
        "statuses": Dataset.Status.choices,
    }
    return render(request, "census/dataset_list.html", context)


def dataset_context(dataset: Dataset, tab: str) -> dict:
    return {"dataset": dataset, "tab": tab}


def dataset_detail(request: HttpRequest, pk: int) -> HttpResponse:
    dataset = get_object_or_404(
        Dataset.objects.select_related("source__publisher", "jurisdiction", "service"), pk=pk
    )
    observations = list(DatasetObservation.objects.filter(dataset=dataset).order_by("observed_at"))
    schema_changes = [o for o in observations if o.fields is not None][1:]
    counts = [(o.observed_at, o.feature_count) for o in observations if o.feature_count is not None]
    context = dataset_context(dataset, "overview") | {
        "observations": observations,
        "schema_changes": schema_changes,
        "count_series": json.dumps([[t.isoformat(), c] for t, c in counts]),
        "harvests": dataset.harvests.all()[:10],
        "related": Dataset.objects.filter(source=dataset.source, service=dataset.service)
        .exclude(pk=dataset.pk)
        .filter(removed_at=None)[:20],
    }
    return render(request, "census/dataset_detail.html", context)


def preview_geojson(df) -> dict:
    """GeoJSON for a preview frame; attributes go through pandas so dates become ISO strings."""
    has_geometry = "geometry" in df.columns
    attrs = df.drop(columns="geometry") if has_geometry else df
    records = json.loads(attrs.to_json(orient="records", date_format="iso"))
    geometries = (
        [mapping(g) if g is not None else None for g in df.geometry]
        if has_geometry
        else [None] * len(records)
    )
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": props, "geometry": geom}
            for props, geom in zip(records, geometries, strict=True)
        ],
    }


def dataset_preview(request: HttpRequest, pk: int) -> HttpResponse:
    """A live sample straight from the source, as GeoJSON, for datasets not yet harvested."""
    dataset = get_object_or_404(Dataset, pk=pk)
    try:
        df = client.preview_features(client.make_session(retries=1), dataset.url, limit=500)
    except Exception as e:
        return JsonResponse({"error": f"{type(e).__name__}: {e}"}, status=502)
    return JsonResponse(preview_geojson(df), content_type="application/geo+json")


@require_POST
def dataset_watch(request: HttpRequest, pk: int) -> HttpResponse:
    dataset = get_object_or_404(Dataset, pk=pk)
    watching = request.POST.get("watch") == "1"
    dataset.status = Dataset.Status.WATCHED if watching else Dataset.Status.DISCOVERED
    dataset.save(update_fields=["status"])
    if watching and not dataset.harvests.exists():
        start_harvest(dataset)
        messages.success(request, "Watching. The first harvest is queued.")
    else:
        messages.success(request, "Watching." if watching else "No longer watching.")
    return redirect(dataset)


@require_POST
def dataset_harvest(request: HttpRequest, pk: int) -> HttpResponse:
    dataset = get_object_or_404(Dataset, pk=pk)
    start_harvest(dataset, force=request.POST.get("force") == "1")
    messages.success(request, "Harvest queued. Progress is on the Jobs page.")
    return redirect(dataset)


def place_list(request: HttpRequest) -> HttpResponse:
    places = (
        Jurisdiction.objects.current()
        .annotate(
            n_datasets=Count(
                "datasets",
                filter=Q(datasets__removed_at=None),
                distinct=True,
            )
        )
        # governments, plus any other place (a school district, a community) with datasets
        .filter(Q(kind__in=["state", "county", "place", "tribal"]) | Q(n_datasets__gt=0))
        .order_by("kind", "name")
        .defer("boundary")
    )
    groups = {}
    for p in places:
        groups.setdefault(p.get_kind_display(), []).append(p)
    return render(request, "census/place_list.html", {"groups": groups})


def place_detail(request: HttpRequest, slug: str) -> HttpResponse:
    place = get_object_or_404(Jurisdiction, slug=slug)
    datasets = (
        Dataset.objects.filter(jurisdiction=place, removed_at=None)
        .select_related("source")
        .prefetch_related("topics")
        .order_by("name")
    )
    by_topic: dict[str, list] = {}
    for d in datasets:
        for t in d.topics.all() or [None]:
            by_topic.setdefault(t.name if t else "Other", []).append(d)
    topic_order = {t.name: t.order for t in Topic.objects.all()}
    by_topic = dict(sorted(by_topic.items(), key=lambda kv: topic_order.get(kv[0], 999)))
    covered = set(by_topic)
    context = {
        "place": place,
        "datasets": datasets,
        "by_topic": by_topic,
        "missing_topics": [t for t in Topic.objects.all() if t.name not in covered],
        "sources": Source.objects.filter(Q(datasets__jurisdiction=place) | Q(jurisdiction=place))
        .distinct()
        .order_by("name"),
        "publishers": Publisher.objects.filter(jurisdiction=place),
        "children": place.children.annotate(
            n_datasets=Count("datasets", filter=Q(datasets__removed_at=None))
        ).defer("boundary"),
        "boundary": place.boundary.geojson if place.boundary else "null",
    }
    return render(request, "census/place_detail.html", context)


def coverage(request: HttpRequest) -> HttpResponse:
    """Counties x topics: how many datasets each county publishes on each subject."""
    topics = list(Topic.objects.all())
    counties = list(
        Jurisdiction.objects.current().filter(kind="county").order_by("name").defer("boundary")
    )
    counts = {
        (row["jurisdiction_id"], row["topics__id"]): row["n"]
        for row in Dataset.objects.filter(removed_at=None, jurisdiction__kind="county")
        .values("jurisdiction_id", "topics__id")
        .annotate(n=Count("id", distinct=True))
    }
    totals = {
        row["jurisdiction_id"]: row["n"]
        for row in Dataset.objects.filter(removed_at=None, jurisdiction__kind="county")
        .values("jurisdiction_id")
        .annotate(n=Count("id"))
    }
    rows = [
        {
            "county": c,
            "total": totals.get(c.pk, 0),
            "cells": [counts.get((c.pk, t.pk), 0) for t in topics],
        }
        for c in counties
    ]
    rows.sort(key=lambda r: (-r["total"], r["county"].name))
    topic_totals = [sum(1 for r in rows if r["cells"][i]) for i in range(len(topics))]
    return render(
        request,
        "census/coverage.html",
        {"topics": topics, "rows": rows, "topic_totals": topic_totals, "n_counties": len(counties)},
    )


def counties_geojson(request: HttpRequest) -> JsonResponse:
    """Simplified county boundaries with dataset counts, for the home and coverage maps."""
    topic = request.GET.get("topic")
    count_filter = Q(datasets__removed_at=None)
    if topic:
        count_filter &= Q(datasets__topics__slug=topic)
    counties = (
        Jurisdiction.objects.current()
        .filter(kind="county")
        .annotate(
            n=Count("datasets", filter=count_filter, distinct=True),
            geom=AsGeoJSON("boundary", precision=4),
        )
        .values("name", "slug", "n", "geom")
    )
    features = [
        {
            "type": "Feature",
            "properties": {"name": c["name"], "slug": c["slug"], "n": c["n"]},
            "geometry": json.loads(c["geom"]),
        }
        for c in counties
    ]
    return JsonResponse({"type": "FeatureCollection", "features": features})


def source_list(request: HttpRequest) -> HttpResponse:
    sources = Source.objects.select_related("publisher", "jurisdiction").annotate(
        n_datasets=Count(
            "datasets",
            filter=Q(datasets__removed_at=None),
            distinct=True,
        ),
        n_watched=Count("datasets", filter=Q(datasets__status="watched"), distinct=True),
    )
    # One table per platform, in the order Platform lists them; crawlable ones first
    groups = [
        {
            "label": platform.label,
            "crawlable": platform in Source.CRAWLABLE,
            "sources": [s for s in sources if s.platform == platform],
        }
        for platform in Source.Platform
    ]
    groups = sorted((g for g in groups if g["sources"]), key=lambda g: not g["crawlable"])
    return render(request, "census/source_list.html", {"groups": groups})


def source_add(request: HttpRequest) -> HttpResponse:
    """Paste a URL, review what the server publishes and where it's from, then crawl it."""
    url = (request.POST if request.method == "POST" else request.GET).get("url", "").strip()
    if not url:
        return redirect("census:source_list")
    for root in new_sources.candidate_roots(url):
        if existing := new_sources.existing_source(root):
            messages.error(request, f"{root} is already in the census as {existing.name}.")
            return redirect(existing)
    try:
        found = new_sources.discover(url)
    except new_sources.NotADirectory as e:
        messages.error(request, str(e))
        return redirect("census:source_list")
    if existing := new_sources.existing_source(found.url):
        messages.error(request, f"{found.url} is already in the census as {existing.name}.")
        return redirect(existing)

    if request.method == "POST":
        form = {k: request.POST.get(k, "").strip() for k in SOURCE_FORM_FIELDS}
        rows = list(zip(request.POST.getlist("key"), request.POST.getlist("place"), strict=False))
        errors, source = create_source(found, form, rows)
        if source:
            start_crawl(source)
            messages.success(request, "Source added and crawl queued. Watch it on the Jobs page.")
            return redirect(source)
    else:
        form = {"name": new_sources.default_name(found.url)}
        rows = [(key, j.slug) for key, (j, _) in found.places.items()]
        errors = []

    counts = {key: n for key, (_, n) in found.places.items()}
    return render(
        request,
        "census/source_add.html",
        {
            "found": found,
            "form": form,
            "rows": [(key, slug, counts.get(key)) for key, slug in rows if key or slug],
            "errors": errors,
            "jurisdictions": Jurisdiction.objects.current()
            .governments()
            .order_by("name")
            .only("slug", "name"),
            "publishers": Publisher.objects.values_list("name", flat=True),
        },
    )


SOURCE_FORM_FIELDS = ("name", "publisher", "jurisdiction", "found_via")


def create_source(found, form: dict, rows: list[tuple[str, str]]) -> tuple[list, Source | None]:
    errors = []
    slugs = set(Jurisdiction.objects.values_list("slug", flat=True))
    if not form["name"]:
        errors.append("Give the source a name.")
    if form["jurisdiction"] and form["jurisdiction"] not in slugs:
        errors.append(f"No place with the slug {form['jurisdiction']!r}.")
    mapping = {}
    for key, slug in rows:
        key, slug = key.strip(), slug.strip()
        if not (key and slug):
            continue
        if slug not in slugs:
            errors.append(f"No place with the slug {slug!r} (for {key}).")
        mapping[key] = slug
    if errors:
        return errors, None

    with transaction.atomic():
        publisher = None
        if form["publisher"]:
            publisher, _ = Publisher.objects.get_or_create(name=form["publisher"])
        source = Source.objects.create(
            name=form["name"],
            slug=new_sources.unique_slug(form["name"]),
            url=found.url,
            platform=found.platform,
            publisher=publisher,
            jurisdiction=Jurisdiction.objects.filter(slug=form["jurisdiction"]).first(),
            folder_jurisdictions=mapping,
            found_via=form["found_via"],
        )
    return [], source


def source_detail(request: HttpRequest, slug: str) -> HttpResponse:
    source = get_object_or_404(
        Source.objects.select_related("publisher", "jurisdiction"), slug=slug
    )
    services = source.services.annotate(
        n_datasets=Count("datasets", filter=Q(datasets__removed_at=None))
    ).filter(removed_at=None)
    return render(
        request,
        "census/source_detail.html",
        {
            "source": source,
            "crawls": source.crawls.all()[:15],
            "services": services,
            "error_services": [s for s in services if s.error],
            "n_datasets": source.datasets.filter(removed_at=None).count(),
        },
    )


@require_POST
def source_crawl(request: HttpRequest, slug: str) -> HttpResponse:
    source = get_object_or_404(Source, slug=slug)
    if not source.crawlable:
        messages.error(request, f"The hub can't crawl {source.get_platform_display()} sources.")
        return redirect(source)
    start_crawl(source)
    messages.success(request, "Crawl queued. Progress is on the Jobs page.")
    return redirect(source)


def changes(request: HttpRequest) -> HttpResponse:
    """What's new: datasets added or removed by crawls, and harvests that found changes."""
    from harvest.models import Harvest

    new = Dataset.objects.select_related("source", "jurisdiction").order_by("-first_seen")[:50]
    removed = (
        Dataset.objects.exclude(removed_at=None)
        .select_related("source", "jurisdiction")
        .order_by("-removed_at")[:50]
    )
    harvests = (
        Harvest.objects.filter(status="succeeded")
        .filter(Q(added__gt=0) | Q(changed__gt=0) | Q(removed__gt=0))
        .select_related("dataset__jurisdiction")
        .order_by("-started_at")[:50]
    )
    crawls = Crawl.objects.select_related("source")[:30]
    return render(
        request,
        "census/changes.html",
        {"new": new, "removed": removed, "harvests": harvests, "crawls": crawls},
    )
