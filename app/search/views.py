from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from census.models import Dataset
from search import index
from search.models import NameEntry

NAME_LIMIT = 50


def name_search(request: HttpRequest) -> HttpResponse:
    q = request.GET.get("q", "").strip()
    current_only = request.GET.get("current") == "1"
    groups = index.search(q, limit=NAME_LIMIT, current_only=current_only) if q else []
    context = {
        "q": q,
        "current_only": current_only,
        "groups": groups,
        "n_records": sum(g["n_records"] for g in groups),
        "truncated": len(groups) >= NAME_LIMIT,
        "n_names": NameEntry.objects.count(),
        "indexed_datasets": Dataset.objects.filter(name_entries__isnull=False).distinct(),
    }
    template = (
        "search/_results.html" if request.headers.get("HX-Request") else "search/name_search.html"
    )
    return render(request, template, context)
