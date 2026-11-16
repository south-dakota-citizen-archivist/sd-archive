"""Finders: look for GIS servers the census doesn't have yet, and list each as a candidate
for review (SourceCandidate).

- arcgis_online: public items on ArcGIS Online inside South Dakota. Hosted layers name their
  organization's services directory, and web maps name every layer they draw, wherever it's
  served from, so a city's own server turns up through any map that uses it.
- hosts: South Dakota governments' web domains (CISA's .gov list, Wikidata's official
  websites, the census's own sources), the host names certificate logs show under them, and
  the usual GIS host names (gis., maps., arcgis. …), each checked for a services directory.

A finder returns Leads; record() turns them into candidates. A lead for a server that's
already a source is counted and dropped; a candidate seen again gets the new evidence, and
one already rejected stays rejected.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

logger = logging.getLogger(__name__)

EVIDENCE_KEPT = 25  # examples kept per finder; counts keep the full number


@dataclass
class Lead:
    url: str  # the services root
    found_via: str  # the finder
    title: str = ""
    platform: str = ""
    evidence: list[dict] = field(default_factory=list)


def merge(leads: Iterable[Lead]) -> list[Lead]:
    """One lead per root (by its lowercased address), its evidence pooled."""
    out: dict[str, Lead] = {}
    for lead in leads:
        key = lead.url.rstrip("/").split("://", 1)[-1].lower()
        if key in out:
            out[key].evidence += lead.evidence
            out[key].title = out[key].title or lead.title
        else:
            out[key] = Lead(lead.url.rstrip("/"), lead.found_via, lead.title, lead.platform,
                            list(lead.evidence))  # fmt: skip
    return list(out.values())


def record(leads: Iterable[Lead]) -> dict:
    """Write leads as candidates. Returns counts: new, seen again, already sources."""
    from census.models import SourceCandidate
    from census.sources import existing_source, platform_for

    counts = {"new": 0, "seen_again": 0, "already_a_source": 0}
    now = timezone.now()
    for lead in merge(leads):
        rest = lead.url.split("://", 1)[-1]
        source = existing_source(lead.url)
        found = SourceCandidate.objects.filter(
            Q(url__iexact=f"https://{rest}") | Q(url__iexact=f"http://{rest}")
        ).first()
        if source:
            counts["already_a_source"] += 1
            if found and found.status == SourceCandidate.Status.PENDING:
                found.status, found.source, found.reviewed_at = "accepted", source, now
                found.save(update_fields=["status", "source", "reviewed_at"])
            continue
        if found is None:
            found = SourceCandidate(url=lead.url, title=lead.title[:500],
                                    platform=lead.platform or platform_for(lead.url),
                                    found_via=lead.found_via, evidence={})  # fmt: skip
            counts["new"] += 1
        else:
            counts["seen_again"] += 1
            vias = found.found_via.split(", ")
            if lead.found_via not in vias:
                found.found_via = ", ".join([*vias, lead.found_via])[:255]
            found.title = found.title or lead.title[:500]
        examples = found.evidence.setdefault("examples", {})
        newest = sorted(lead.evidence, key=lambda e: e.get("modified") or "", reverse=True)
        examples[lead.found_via] = newest[:EVIDENCE_KEPT]  # the most recently modified
        found.evidence.setdefault("counts", {})[lead.found_via] = len(lead.evidence)
        note_latest(found, lead.evidence)
        found.last_seen_at = now
        found.save()
    return counts


def note_latest(candidate, evidence: list[dict]) -> None:
    """Keep the most recently modified item among a candidate's evidence (all of it, not
    just the examples kept), across finders: evidence["latest"] = {modified, item, title}."""
    dated = [e for e in evidence if e.get("modified")]
    if not dated:
        return
    newest = max(dated, key=lambda e: e["modified"])
    had = candidate.evidence.get("latest") or {}
    if newest["modified"] > (had.get("modified") or ""):
        candidate.evidence["latest"] = {k: newest.get(k, "") for k in ("modified", "item",
                                                                        "title")}  # fmt: skip


def cache_dir(name: str) -> Path:
    path = Path(settings.MEDIA_ROOT) / "raw" / "gis-finders" / name
    path.mkdir(parents=True, exist_ok=True)
    return path


class Getter:
    """GET JSON (or text) with the census's browser-like session, a pause between requests,
    and waits on 429 and 5xx answers (honoring Retry-After)."""

    def __init__(self, pause: float = 1.0, timeout: float = 60.0, tries: int = 4,
                 sleep: Callable[[float], None] = time.sleep):  # fmt: skip
        from sdgis.arcgis import client

        self.session = client.make_session(retries=0)
        self.pause, self.timeout, self.tries, self.sleep = pause, timeout, tries, sleep

    def text(self, url: str, params: dict | None = None, timeout: float | None = None,
             headers: dict | None = None) -> str:  # fmt: skip
        for attempt in range(self.tries):
            self.sleep(self.pause)
            response = self.session.get(url, params=params, timeout=timeout or self.timeout,
                                        headers=headers)  # fmt: skip
            if response.status_code == 429 or response.status_code >= 500:
                wait = response.headers.get("Retry-After", "")
                wait = float(wait) if wait.isdigit() else 30.0 * 2**attempt
                logger.info("%s: HTTP %s, waiting %.0f s", url, response.status_code, wait)
                self.sleep(min(wait, 600))
                continue
            response.raise_for_status()
            return response.text
        raise RuntimeError(f"{url}: no answer after {self.tries} tries")

    def json(self, url: str, params: dict | None = None, **kwargs):
        return json.loads(self.text(url, params, **kwargs))
