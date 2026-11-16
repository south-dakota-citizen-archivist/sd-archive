"""Blue Book parts the readings leave unconfirmed, for the review pages (review.providers):
each page image beside its readings. A decision is saved to fixes.toml (sddocs.review,
keyed by the image ID and the part) and the record is rebuilt at once."""

from __future__ import annotations

import json
import re
from pathlib import Path

from django.http import FileResponse, Http404, HttpRequest
from django.urls import reverse

from sddocs import review

from . import source

DECISIONS = Path(__file__).with_name("fixes.toml")
READERS = (("legislature", "The Legislature's OCR"), ("ours", "rapidocr"),
           ("vision", "Apple Vision"))  # fmt: skip


def _store():
    from sdfetch import Store

    return Store(source.raw_root())


class BioReview:
    slug = source.SOURCE.slug
    name = source.SOURCE.name
    decisions = DECISIONS
    applies = "at once (the record is rebuilt)"

    def items(self) -> list[review.Item]:
        from records.models import Record

        out = []
        records = (Record.objects.filter(recordset__slug=self.slug, dropped_at__isnull=True,
                                         data__has_key="unconfirmed")
                   .exclude(data__unconfirmed=[]))  # fmt: skip
        for record in sorted(records, key=lambda r: (r.data.get("year") or 0, r.data["member"])):
            d = record.data
            for part in d["unconfirmed"]:
                r = d["readings"].get(part, {})
                out.append(review.Item(
                    source=self.slug, key=record.key, kind="pick", title=d["member"],
                    subtitle=f"{d['book']}{', page ' + str(d['page']) if d.get('page') else ''}",
                    links=[{"label": "record", "url": reverse("records:record_detail",
                                                              args=[self.slug, record.pk])},
                           {"label": "profile", "url": record.source_url}],
                    image={"url": reverse("records:bio_image", args=[record.key])},
                    field=part, label=part.replace("_", " "),
                    readings={k: r.get(k) for k, _ in READERS if k in r}))  # fmt: skip
        return out

    def saved(self, key: str) -> None:
        from records.models import Record

        record = Record.objects.filter(recordset__slug=self.slug, key=key).first()
        if record:
            rebuild(record)

    def check(self, item, cells):
        return None

    def decided(self) -> dict[tuple[str, str], dict]:
        return review.load(self.decisions)


REVIEW = BioReview()


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
