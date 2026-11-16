"""The review pages: what every source has waiting for a person, a document's page image
beside its readings, and the decisions (saved to the source's versioned decisions file)."""

from __future__ import annotations

import io

from django.contrib import messages
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.html import escape
from django.utils.safestring import mark_safe
from django.views.decorators.http import require_POST

from review.providers import pdf_path, providers
from sddocs import review


def _provider(slug: str):
    found = providers().get(slug)
    if found is None:
        raise Http404("no such source")
    return found


def marked(value, others: list) -> str:
    """A reading with the words the other readings don't all have marked."""
    if value is None or value == "":
        return mark_safe('<span class="text-gray-400">nothing read</span>')
    words = [set(str(o).split()) for o in others if o not in (None, "")]
    out = []
    for w in str(value).split():
        differs = any(w not in ws for ws in words)
        out.append(f'<mark class="bg-amber-100 rounded-sm">{escape(w)}</mark>' if differs
                   else escape(w))  # fmt: skip
    return mark_safe(" ".join(out))


def show(value, kind: str = "text") -> str:
    """A cell's value as the report prints it."""
    if value is None:
        return ""
    if kind == "money":
        return f"(${-value:,.2f})" if value < 0 else f"${value:,.2f}"
    if kind == "pct":
        return f"{value:.2f}%"
    if kind == "int":
        return str(value)
    return str(value)


def _groups(provider) -> list[dict]:
    """The provider's items by document key, in the order it gives them."""
    decided = provider.decided()
    groups: dict[str, dict] = {}
    for item in provider.items():
        g = groups.setdefault(item.key, {"key": item.key, "title": item.title,
                                         "subtitle": item.subtitle, "items": [], "fields": 0,
                                         "decided": 0})  # fmt: skip
        g["items"].append(item)
        fields = ([item.field] if item.kind == "pick" else
                  [c["field"] for s in item.sections for r in s["rows"]
                   for c in r["cells"].values()])  # fmt: skip
        g["fields"] += 1
        if item.kind == "pick":
            g["decided"] += (item.key, item.field) in decided
        else:
            g["decided"] += any((item.key, f) in decided for f in [*fields, "*"])
    return list(groups.values())


def index(request: HttpRequest) -> HttpResponse:
    sources = []
    chosen = request.GET.get("source")
    for slug, p in providers().items():
        groups = _groups(p)
        sources.append({"slug": slug, "name": p.name, "groups": groups,
                        "waiting": sum(g["fields"] - g["decided"] for g in groups),
                        "decided": len(p.decided()), "applies": p.applies,
                        "decisions": p.decisions})  # fmt: skip
    shown = [s for s in sources if not chosen or s["slug"] == chosen]
    return render(request, "review/index.html", {"sources": sources, "shown": shown,
                                                 "chosen": chosen})  # fmt: skip


def _readings_title(readings: dict, kind: str) -> str:
    return "; ".join(f"{k}: {', '.join(show(v, kind) or 'N/A' for v in vs)}"
                     for k, vs in readings.items())  # fmt: skip


def _table(item: review.Item, decided: dict) -> list[dict]:
    """A table item's sections with each cell's value (a decision's, where there is one)
    and its readings for display."""
    out = []
    for section in item.sections:
        rows = []
        for row in section["rows"]:
            cells = []
            for col in section["columns"]:
                cell = row["cells"].get(col["key"])
                if cell is None:
                    cells.append(None)
                    continue
                d = decided.get((item.key, cell["field"]))
                value = d.get("value") if d else cell.get("value")
                readings = cell.get("readings") or {}
                options = sorted({show(v, col["kind"]) for vs in readings.values() for v in vs
                                  if v is not None})  # fmt: skip
                cells.append(
                    {
                        **cell,
                        "kind": col["kind"],
                        "text": show(value, col["kind"]),
                        "was": show(cell.get("value"), col["kind"]),
                        "decision": d,
                        "options": options,
                        "differs": len(
                            {show(v, col["kind"]) for vs in readings.values() for v in vs}
                        )
                        > 1,
                        "title": _readings_title(readings, col["kind"]),
                    }
                )
            rows.append({"name": row["name"], "cells": cells})
        out.append({**section, "rows": rows})
    return out


def detail(request: HttpRequest, source: str, key: str) -> HttpResponse:
    p = _provider(source)
    items = [i for i in p.items() if i.key == key]
    if not items:
        raise Http404("nothing waiting for this document")
    decided = p.decided()
    picks, tables = [], []
    for item in items:
        if item.kind == "table":
            tables.append({"item": item, "sections": _table(item, decided),
                           "decision": decided.get((key, "*"))})  # fmt: skip
            continue
        kind = item.context.get("kind", "text")
        texts = {k: show(v, kind) for k, v in item.readings.items()}
        decision = decided.get((key, item.field))
        picks.append({"item": item, "decision": decision,
                      "decided": show(decision.get("value"), kind) if decision else "",
                      "readings": [{"key": k, "value": t,
                                    "marked": marked(t, [o for j, o in texts.items() if j != k])
                                    if t else marked(None, [])}
                                   for k, t in texts.items()]})  # fmt: skip
    keys = [g["key"] for g in _groups(p)]
    at = keys.index(key)
    return render(request, "review/detail.html", {
        "provider": p, "first": items[0], "picks": picks, "tables": tables,
        "next": keys[at + 1] if at + 1 < len(keys) else None,
        "previous": keys[at - 1] if at else None, "position": at + 1,
        "total": len(keys)})  # fmt: skip


def _cells(request: HttpRequest, item: review.Item) -> tuple[dict, dict]:
    """A table form's cell values (parsed) and which of them changed from the source's."""
    values, changed = {}, {}
    kinds = {c["field"]: col["kind"] for s in item.sections for col in s["columns"]
             for r in s["rows"] for k, c in r["cells"].items() if k == col["key"]}  # fmt: skip
    for s in item.sections:
        for r in s["rows"]:
            for c in r["cells"].values():
                text = request.POST.get(f"cell:{c['field']}")
                if text is None:
                    continue
                value = review.parse_value(text, kinds[c["field"]])
                values[c["field"]] = value
                if text.strip() != request.POST.get(f"was:{c['field']}", "").strip():
                    changed[c["field"]] = (value, c.get("readings") or {})
    return values, changed


@require_POST
def decide(request: HttpRequest, source: str, key: str) -> HttpResponse:
    p = _provider(source)
    items = {(i.kind, i.field): i for i in p.items() if i.key == key}
    reason = request.POST.get("reason", "").strip()
    here = reverse("review:detail", args=[source, key])
    try:
        if request.POST.get("kind") == "table":
            item = next(i for (kind, _), i in items.items() if kind == "table")
            _, changed = _cells(request, item)
            if not reason:
                raise review.ReviewError("a decision needs a reason: what settles the problems")
            for field, (value, readings) in changed.items():
                chose = next((k for k, vs in readings.items() if value in vs), review.FROM_TYPED)
                review.save(p.decisions, {"key": key, "field": field, "value": value,
                                          "from": chose, "reason": reason})  # fmt: skip
            if not changed:  # the table as read is right (the report's own sums are off)
                review.save(p.decisions, {"key": key, "field": "*", "from": "as read",
                                          "reason": reason})  # fmt: skip
            note = f"{len(changed)} cell{'s' if len(changed) != 1 else ''}" if changed else (
                "the table as read")  # fmt: skip
        else:
            field = request.POST.get("field", "")
            item = items.get(("pick", field))
            if item is None:
                raise review.ReviewError("that field isn't waiting for a decision")
            chose = request.POST.get("from") or review.FROM_TYPED
            kind = item.context.get("kind", "text")
            text = request.POST.get("value", "").strip()
            if chose != review.FROM_NONE and not text:
                raise review.ReviewError("choose a reading, type the value, or leave it empty")
            value = review.parse_value(text, kind) if chose != review.FROM_NONE else None
            if chose not in (review.FROM_NONE, review.FROM_TYPED) and (
                item.readings.get(chose) != value
            ):
                chose = review.FROM_TYPED  # a reading, edited
            review.save(p.decisions, {"key": key, "field": field,
                                      "value": value if chose != review.FROM_NONE else None,
                                      "from": chose, "reason": reason})  # fmt: skip
            note = field.replace("_", " ")
    except (review.ReviewError, StopIteration) as e:
        messages.error(request, str(e) or "nothing to decide")
        return redirect(here)
    p.saved(key)
    messages.success(request, f"Saved {note} to {p.decisions.name}; it applies {p.applies}.")
    return redirect(here)


@require_POST
def undo(request: HttpRequest, source: str, key: str) -> HttpResponse:
    p = _provider(source)
    field = request.POST.get("field", "")
    fields = [f for k, f in p.decided() if k == key] if field == "*all" else [field]
    if [review.remove(p.decisions, key, f) for f in fields if f]:
        p.saved(key)
        messages.success(request, "Decision removed.")
    return redirect(reverse("review:detail", args=[source, key]))


@require_POST
def check(request: HttpRequest, source: str, key: str) -> HttpResponse:
    p = _provider(source)
    item = next((i for i in p.items() if i.key == key and i.kind == "table"), None)
    if item is None:
        raise Http404
    try:
        values, changed = _cells(request, item)
        problems = p.check(item, values)
    except review.ReviewError as e:
        problems, changed = [str(e)], {}
    return render(request, "review/_problems.html", {"problems": problems, "checked": True,
                                                     "changed": len(changed)})  # fmt: skip


def page_image(request: HttpRequest) -> HttpResponse:
    """One page of a pipeline's stored PDF, as a PNG."""
    import pypdfium2 as pdfium

    try:
        path = pdf_path(request.GET.get("pdf", ""))
        index = int(request.GET.get("page", "0"))
    except ValueError as e:
        raise Http404 from e
    doc = pdfium.PdfDocument(str(path))
    try:
        if not 0 <= index < len(doc):
            raise Http404
        image = doc[index].render(scale=2).to_pil()
    finally:
        doc.close()
    buf = io.BytesIO()
    image.save(buf, "PNG", optimize=True)
    response = HttpResponse(buf.getvalue(), content_type="image/png")
    response["Cache-Control"] = "max-age=86400"
    return response
