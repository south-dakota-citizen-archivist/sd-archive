"""Pages for every record set: the list, each set's overview and table, its runs, and each
record with its documents, places, mentions and links."""

from __future__ import annotations

import csv
import json
from collections import Counter

from django.core.paginator import Paginator
from django.db.models import Case, Count, F, FloatField, OuterRef, Subquery, TextField, When
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Cast, Length
from django.http import FileResponse, Http404, HttpRequest, HttpResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, render

from records.models import Document, Record, RecordSet, Run

PAGE_SIZE = 50
SAMPLE = 300  # records read to find a set's fields
TABLE_COLUMNS = 10  # fields shown in the table until others are chosen
NUMBER = r"^-?[0-9]+(\.[0-9]+)?$"


# --- helpers ---------------------------------------------------------------------------


def fields_of(recordset: RecordSet) -> list[dict]:
    """A record set's top-level fields (in the order the database keeps them), from a sample:
    how often each is filled, whether it's a number, whether it holds nested values, an
    example."""
    seen: dict[str, dict] = {}
    # no ordering: the model's default (by record set name, key) sorts the whole set
    rows = Record.objects.filter(recordset=recordset).order_by().values_list("data", flat=True)
    rows = rows[:SAMPLE]
    n = 0
    for data in rows:
        n += 1
        for name, value in (data or {}).items():
            f = seen.setdefault(name, {"name": name, "filled": 0, "numbers": 0, "nested": 0,
                                       "values": 0, "example": None})  # fmt: skip
            if value in (None, "", [], {}):
                continue
            f["filled"] += 1
            f["values"] += 1
            if isinstance(value, bool):
                pass
            elif isinstance(value, int | float):
                f["numbers"] += 1
            elif isinstance(value, dict | list) and not _flat_list(value):
                f["nested"] += 1
            if f["example"] is None:
                f["example"] = cell(value, 80)
    for f in seen.values():
        f["share"] = round(100 * f["filled"] / n) if n else 0
        f["numeric"] = bool(f["values"]) and f["numbers"] == f["values"]
        f["is_nested"] = bool(f["values"]) and f["nested"] * 2 > f["values"]
    return list(seen.values())


def _flat_list(value) -> bool:
    return isinstance(value, list) and all(not isinstance(v, dict | list) for v in value)


def cell(value, limit: int | None = 120) -> str:
    """A value as one line of text: lists of plain values joined, anything nested as JSON."""
    if value is None:
        return ""
    if isinstance(value, bool):
        text = "yes" if value else "no"
    elif _flat_list(value):
        text = ", ".join("" if v is None else str(v) for v in value)
    elif isinstance(value, dict | list):
        text = json.dumps(value, ensure_ascii=False, separators=(", ", ": "))
    else:
        text = str(value)
    return text if limit is None or len(text) <= limit else text[: limit - 1] + "…"


def counts_by_set() -> dict[int, dict]:
    current = dict(
        Record.objects.current().values_list("recordset").annotate(n=Count("*")).order_by()
    )
    dropped = dict(
        Record.objects.dropped().values_list("recordset").annotate(n=Count("*")).order_by()
    )
    documents = dict(
        Document.objects.values_list("record__recordset").annotate(n=Count("id")).order_by()
    )
    return {pk: {"current": current.get(pk, 0), "dropped": dropped.get(pk, 0),
                 "documents": documents.get(pk, 0)}
            for pk in set(current) | set(dropped) | set(documents)}  # fmt: skip


def get_recordset(slug: str) -> RecordSet:
    return get_object_or_404(
        RecordSet.objects.select_related("publisher", "gis_dataset"), slug=slug
    )


def tab_context(recordset: RecordSet, tab: str) -> dict:
    return {"recordset": recordset, "tab": tab}


# --- record sets -----------------------------------------------------------------------


def recordset_list(request: HttpRequest) -> HttpResponse:
    last_run = Run.objects.filter(recordset=OuterRef("pk")).order_by("-started_at")
    sets = RecordSet.objects.select_related("publisher", "gis_dataset").annotate(
        last_run_at=Subquery(last_run.values("started_at")[:1]),
        last_run_status=Subquery(last_run.values("status")[:1]),
    )
    counts = counts_by_set()
    rows = [(rs, counts.get(rs.pk, {"current": 0, "dropped": 0, "documents": 0})) for rs in sets]
    totals = Counter()
    for _, c in rows:
        totals.update(c)
    return render(request, "records/recordset_list.html", {"rows": rows, "totals": totals})


def recordset_detail(request: HttpRequest, slug: str) -> HttpResponse:
    recordset = get_recordset(slug)
    records = Record.objects.filter(recordset=recordset)
    documents = (
        Document.objects.filter(record__recordset=recordset)
        .values_list("status")
        .annotate(n=Count("id"))
        .order_by()
    )
    context = (
        tab_context(recordset, "overview")
        | {
            "current": records.current().count(),
            "dropped": records.dropped().count(),
            "documents": [
                (dict(Document.Status.choices).get(status, status), n)
                for status, n in sorted(documents, key=lambda d: -d[1])
            ],  # fmt: skip
            "runs": recordset.runs.select_related("job")[:25],
            "fields": fields_of(recordset),
        }
    )
    return render(request, "records/recordset_detail.html", context)


def _table_columns(request: HttpRequest, fields: list[dict]) -> list[dict]:
    chosen = [c for c in request.GET.get("cols", "").split(",") if c]
    by_name = {f["name"]: f for f in fields}
    if chosen:
        return [by_name[c] for c in chosen if c in by_name]
    plain = [f for f in fields if not f["is_nested"]]
    return plain[:TABLE_COLUMNS]


def recordset_table(request: HttpRequest, slug: str) -> HttpResponse:
    recordset = get_recordset(slug)
    fields = fields_of(recordset)
    columns = _table_columns(request, fields)
    q = request.GET.get("q", "").strip()
    field = request.GET.get("field", "")
    value = request.GET.get("value", "").strip()
    show = request.GET.get("show", "current")
    sort = request.GET.get("sort", "")
    desc = request.GET.get("dir") == "desc"

    records = Record.objects.filter(recordset=recordset).only("key", "data", "dropped_at")
    if show == "current":
        records = records.current()
    elif show == "dropped":
        records = records.dropped()
    if q:
        records = records.annotate(data_text=Cast("data", TextField())).filter(
            data_text__icontains=q
        )
    numeric = {f["name"] for f in fields if f["numeric"]}
    if field in {f["name"] for f in fields} and value:
        records = records.annotate(field_text=KeyTextTransform(field, "data")).filter(
            field_text__icontains=value
        )
    if sort in {f["name"] for f in fields}:
        text = KeyTextTransform(sort, "data")
        if sort in numeric:  # numbers sort as numbers; anything else in the column last
            key = Case(When(**{f"data__{sort}__regex": NUMBER},
                            then=Cast(text, FloatField())), default=None)  # fmt: skip
        else:
            key = text
        records = records.annotate(sort_value=key).order_by(
            F("sort_value").desc(nulls_last=True) if desc else F("sort_value").asc(nulls_last=True),
            "key",
        )
    else:
        records = records.order_by(Length("key"), "key")  # "2" before "10"

    page = Paginator(records, PAGE_SIZE).get_page(request.GET.get("page"))
    names = [c["name"] for c in columns]
    context = tab_context(recordset, "table") | {
        "fields": fields,
        "columns": columns,
        "cols": ",".join(names) if request.GET.get("cols") else "",
        "page": page,
        "rows": [(r, [cell((r.data or {}).get(n)) for n in names]) for r in page],
        "q": q,
        "field": field,
        "value": value,
        "show": show,
        "sort": sort,
        "dir": "desc" if desc else "asc",
    }
    template = (
        "records/_table_rows.html"
        if request.headers.get("HX-Request")
        else ("records/recordset_table.html")
    )
    return render(request, template, context)


class _Echo:
    def write(self, value):
        return value


def recordset_csv(request: HttpRequest, slug: str) -> StreamingHttpResponse:
    """Every current record: its key, each field (lists joined, nested values as JSON), its
    source URL, and when it was first seen."""
    recordset = get_recordset(slug)
    names = [f["name"] for f in fields_of(recordset)]
    writer = csv.writer(_Echo())

    def rows():
        yield writer.writerow(["key", *names, "source_url", "first_seen"])
        records = (
            Record.objects.current().filter(recordset=recordset)
            .order_by("pk").values_list("key", "data", "source_url", "first_seen")
        )  # fmt: skip
        for key, data, url, first_seen in records.iterator(chunk_size=5000):
            data = data or {}
            yield writer.writerow(
                [key, *[cell(data.get(n), None) for n in names], url, first_seen.isoformat()]
            )

    response = StreamingHttpResponse(rows(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{recordset.slug}.csv"'
    return response


def run_detail(request: HttpRequest, pk: int) -> HttpResponse:
    run = get_object_or_404(Run.objects.select_related("recordset", "job"), pk=pk)
    return render(
        request,
        "records/run_detail.html",
        {"run": run, "recordset": run.recordset,
         "checks": json.dumps(run.checks, indent=2, ensure_ascii=False) if run.checks else ""},
    )  # fmt: skip


# --- a record --------------------------------------------------------------------------


def record_detail(request: HttpRequest, slug: str, pk: int) -> HttpResponse:
    """A record, by its ID in the archive (keys are whatever text the source uses)."""
    from entities.models import Mention

    recordset = get_recordset(slug)
    record = Record.objects.filter(recordset=recordset, pk=pk).select_related("run").first()
    if record is None:
        raise Http404("no such record")
    fields = [{"name": name, "text": cell(value, None),
               "pre": isinstance(value, dict | list) and not _flat_list(value),
               "json": json.dumps(value, indent=2, ensure_ascii=False)}
              for name, value in (record.data or {}).items()]  # fmt: skip
    documents = record.documents.prefetch_related("extractions").order_by("pk")
    mentions = Mention.objects.filter(
        dataset=recordset.dataset, record_key=f"{recordset.slug}:{record.key}"
    ).order_by("field", "position")
    links_out = record.links_out.select_related("to_record__recordset").order_by("kind", "pk")
    links_in = record.links_in.select_related("from_record__recordset").order_by("kind", "pk")
    return render(
        request,
        "records/record_detail.html",
        {
            "recordset": recordset,
            "record": record,
            "fields": fields,
            "places": record.places.order_by("kind", "name"),
            "documents": documents,
            "mentions": mentions,
            "links_out": links_out,
            "links_in": links_in,
            "geometry": record.geometry.geojson if record.geometry else "null",
            "legacy": json.dumps(record.legacy, indent=2) if record.legacy else "",
        },
    )


def document_file(request: HttpRequest, pk: int) -> FileResponse:
    """A stored document's file, shown in the browser."""
    from records.documents import file_path

    document = get_object_or_404(Document, pk=pk)
    path = file_path(document)
    if path is None or not path.is_file():
        raise Http404("the file isn't stored")
    name = document.filename or path.name
    return FileResponse(path.open("rb"), content_type=document.mime_type or None, filename=name)
