from django.db.models import Count, Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render

from census.models import Crawl, Dataset, Source
from core.models import Job
from places.models import Jurisdiction
from records.models import Record, Run
from search.models import NameEntry


def home(request: HttpRequest) -> HttpResponse:
    live = Dataset.objects.filter(removed_at=None)
    stats = {
        "sources": Source.objects.filter(active=True).count(),
        "datasets": live.count(),
        # isnull=False keeps the join from matching places that have no datasets at all
        "places": Jurisdiction.objects.filter(datasets__isnull=False, datasets__removed_at=None)
        .distinct()
        .count(),
        "counties": Jurisdiction.objects.filter(
            kind="county",
            datasets__isnull=False,
            datasets__removed_at=None,
        )
        .distinct()
        .count(),
        "watched": live.filter(status=Dataset.Status.WATCHED).count(),
        "features": Record.objects.filter(recordset__gis_dataset__isnull=False).count(),
        "names": NameEntry.objects.count(),
    }
    top_topics = (
        live.values("topics__name", "topics__slug")
        .exclude(topics__isnull=True)
        .annotate(n=Count("id", distinct=True))
        .order_by("-n")[:8]
    )
    return render(
        request,
        "core/home.html",
        {
            "stats": stats,
            "top_topics": top_topics,
            "crawls": Crawl.objects.select_related("source")[:5],
            "harvests": Run.objects.filter(recordset__gis_dataset__isnull=False).select_related(
                "recordset__gis_dataset"
            )[:5],
            "watched": live.filter(status=Dataset.Status.WATCHED).select_related("jurisdiction"),
        },
    )


def job_list(request: HttpRequest) -> HttpResponse:
    jobs = Job.objects.all()[:100]
    active = Job.objects.filter(status__in=[Job.Status.PENDING, Job.Status.RUNNING]).exists()
    context = {"jobs": jobs, "active": active}
    if request.headers.get("HX-Request"):
        return render(request, "core/_job_rows.html", context)
    context["counts"] = dict(
        Job.objects.values_list("status").annotate(n=Count("id")).values_list("status", "n")
    )
    context["stale_pending"] = Job.objects.filter(
        Q(status=Job.Status.PENDING) & ~Q(celery_task_id="")
    ).count()
    return render(request, "core/job_list.html", context)


def job_detail(request: HttpRequest, pk: int) -> HttpResponse:
    return render(request, "core/job_detail.html", {"job": get_object_or_404(Job, pk=pk)})
