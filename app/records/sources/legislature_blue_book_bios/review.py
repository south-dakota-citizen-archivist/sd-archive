"""The review page for Blue Book parts the readings leave unconfirmed: each page image beside
its readings; a choice is saved to fixes.toml (decisions.py) and the record rebuilt at once."""

from __future__ import annotations

import json
import re

from django.contrib import messages
from django.http import FileResponse, Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.html import escape
from django.utils.safestring import mark_safe
from django.views.decorators.http import require_POST

from . import decisions, source

SLUG = source.SOURCE.slug
READERS = (("legislature", "The Legislature's OCR"), ("ours", "rapidocr"),
           ("vision", "Apple Vision"))  # fmt: skip


def _store():
    from sdfetch import Store

    return Store(source.raw_root())


def _marked(value: str | None, others: list[str | None]) -> str:
    """A reading with the words the other readings don't all have marked."""
    if not value:
        return mark_safe('<span class="text-gray-400">nothing read</span>')
    words = [set((o or "").split()) for o in others if o]
    out = []
    for w in value.split():
        differs = any(w not in ws for ws in words)
        out.append(f'<mark class="bg-amber-100 rounded-sm">{escape(w)}</mark>' if differs
                   else escape(w))  # fmt: skip
    return mark_safe(" ".join(out))


def page(request: HttpRequest) -> HttpResponse:
    from records.models import Record, RecordSet

    recordset = get_object_or_404(RecordSet, slug=SLUG)
    base = Record.objects.filter(recordset=recordset, dropped_at__isnull=True)
    pages = []
    for record in base.filter(data__has_key="unconfirmed").exclude(data__unconfirmed=[]):
        d = record.data
        parts = []
        for part in d["unconfirmed"]:
            r = d["readings"].get(part, {})
            values = [r.get(k) for k, _ in READERS]
            parts.append({"part": part, "label": part.replace("_", " "), "readings": [
                {"key": k, "label": label, "value": r.get(k),
                 "marked": _marked(r.get(k), [v for v in values if v is not r.get(k)])}
                for k, label in READERS]})  # fmt: skip
        pages.append({"record": record, "d": d, "parts": parts})
    pages.sort(key=lambda p: (p["d"].get("year") or 0, p["d"]["member"]))
    decided = base.filter(data__has_key="by_hand").count()
    return render(request, "records/bio_review.html", {
        "recordset": recordset, "tab": "review", "pages": pages, "decided": decided,
        "n_parts": sum(len(p["parts"]) for p in pages)})  # fmt: skip


def image(request: HttpRequest, image_id: str) -> FileResponse:
    if not re.fullmatch(r"\d+", image_id):
        raise Http404
    path = source.raw_root() / "bio-images" / f"{image_id}.webp"
    if not path.is_file():
        raise Http404("the image isn't stored")
    return FileResponse(path.open("rb"), content_type="image/webp")


def rebuild(record) -> None:
    """Write the record again as a run would, from its stored member response and readings."""
    from records.ingest import _sync_places

    member = json.loads((source.raw_root() / "api" / "Historical" / "Member"
                         / f"{record.data['member_id']}.json").read_text())  # fmt: skip
    picture = next(p for p in member["Pictures"] if source.image_id(p) == record.key)
    item = source.page_item(member, picture, _store())
    record.data, record.content_hash = item.data, item.content_hash
    record.save(update_fields=["data", "content_hash", "updated_at"])
    _sync_places(record.recordset, {record.key: item})


@require_POST
def decide(request: HttpRequest, pk: int) -> HttpResponse:
    from records.models import Record

    record = get_object_or_404(Record, pk=pk, recordset__slug=SLUG)
    part, chose = request.POST.get("part", ""), request.POST.get("from", "")
    if part not in (record.data.get("unconfirmed") or []) or chose not in decisions.FROM:
        messages.error(request, "That part isn't waiting for a decision.")
        return redirect("records:bio_review")
    value = " ".join(request.POST.get("value", "").split()) or None
    if chose == "none":
        value = None
    elif value is None:
        messages.error(request, "Choose a reading, type the value, or leave it empty.")
        return redirect(f"{reverse('records:bio_review')}#r{record.pk}")
    elif chose != "typed" and value != record.data["readings"][part].get(chose):
        chose = "typed"  # a reading, edited
    decisions.save({"image": record.key, "part": part, "value": value, "from": chose,
                    "reason": request.POST.get("reason", "").strip()
                    or "Checked against the page image."})  # fmt: skip
    rebuild(record)
    messages.success(request, f"{record.data['member']}, {record.data['book']}: {part} saved.")
    return redirect(f"{reverse('records:bio_review')}#r{record.pk}")
