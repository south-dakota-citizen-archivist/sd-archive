"""Give GIS candidates their most recently modified South Dakota item, for candidates the
finders recorded before they kept items' dates (a finder run keeps them from then on).

    python manage.py date_candidates

From what the ArcGIS Online finder already has: every web map definition it read is kept
under the map's modified time, so each map's date and the directories its layers name come
from disk; the service items among each candidate's examples are looked up by ID, forty at a
time. Each candidate's examples are dated too and kept newest first.
"""

from __future__ import annotations

import json
import re

from django.core.management.base import BaseCommand

BATCH = 40  # item IDs per search


def _key(url: str) -> str:
    return url.rstrip("/").split("://", 1)[-1].lower()


class Command(BaseCommand):
    help = __doc__

    def handle(self, *args, **options):
        from census.finders import Getter, cache_dir, note_latest
        from census.finders import arcgis_online as ago
        from census.models import SourceCandidate

        found: dict[str, list[dict]] = {}
        maps = 0
        for path in cache_dir("arcgis-online").glob("webmap-*.json"):
            m = re.fullmatch(r"webmap-([0-9a-f]+)-(\d+)\.json", path.name)
            if not m:
                continue
            maps += 1
            definition = json.loads(path.read_text() or "{}")
            for url in dict.fromkeys(ago.layer_urls(definition)):
                root = ago.services_root(url)
                if root:
                    found.setdefault(_key(root), []).append(
                        {"item": m[1], "modified": ago.modified(int(m[2])), "title": ""}
                    )

        candidates = list(SourceCandidate.objects.all())
        service_ids = {e["item"] for c in candidates
                       for es in (c.evidence.get("examples") or {}).values() for e in es
                       if e.get("item") and e.get("type") in ago.SERVICE_TYPES}  # fmt: skip
        get, dates = Getter(pause=0.7), {}
        ids = sorted(service_ids)
        for i in range(0, len(ids), BATCH):
            batch = ids[i : i + BATCH]
            answer = get.json(ago.SEARCH, {"q": " OR ".join(f"id:{x}" for x in batch),
                                           "num": BATCH, "f": "json"})  # fmt: skip
            for item in answer.get("results", []):
                dates[item["id"]] = (ago.modified(item.get("modified")), item.get("title", ""))
        dated = 0
        for c in candidates:
            evidence = list(found.get(_key(c.url), []))
            examples = [e for es in (c.evidence.get("examples") or {}).values() for e in es]
            titles = {e["item"]: e.get("title", "") for e in examples if e.get("item")}
            for e in evidence:
                e["title"] = titles.get(e["item"], "")
            for item in titles:
                if item in dates:
                    evidence.append({"item": item, "modified": dates[item][0],
                                     "title": dates[item][1]})  # fmt: skip
            before = json.dumps(c.evidence, sort_keys=True)
            note_latest(c, evidence)
            # the examples dated too, newest first (as a finder run keeps them)
            known = {e["item"]: e["modified"] for e in evidence}
            for es in (c.evidence.get("examples") or {}).values():
                for e in es:
                    e["modified"] = e.get("modified") or known.get(e.get("item"))
                es.sort(key=lambda e: e.get("modified") or "", reverse=True)
            if json.dumps(c.evidence, sort_keys=True) != before:
                c.save(update_fields=["evidence"])
                dated += 1
        self.stdout.write(
            f"{maps} web maps on disk, {len(dates)} of {len(service_ids)} service "
            f"items looked up; {dated} of {len(candidates)} candidates dated"
        )
