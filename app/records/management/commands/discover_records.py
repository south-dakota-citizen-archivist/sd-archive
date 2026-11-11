"""Suggest record sets to get from a local government's website: crawl it, compile
candidates by rule (platforms, document collections, record types), have a model review
them and read the pages that describe records in prose, and write a report
(media/discovery/<site>/report.json and report.md)."""

import json
import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from records.discovery import review, signals
from records.discovery.crawl import crawl, site_of

PRIORITY = {"high": 0, "medium": 1, "low": 2}


def report_markdown(site: str, start: str, pages: int, suggestions: list[dict],
                    named: list[dict]) -> str:  # fmt: skip
    out = [f"# Records to consider: {site}", "",
           f"Crawled {pages} pages from {start}.", ""]  # fmt: skip

    def line(s):
        where = s["urls"][0] if s.get("urls") else s.get("url", "")
        add = (
            (
                f"; generic extractor: add the government to records.sources._governments for "
                f"{s['extractor']}"
            )
            if s.get("extractor")
            else ""
        )
        return (f"- **{s['title']}** ({s['kind']}, {s['how_to_get']}, still produced: "
                f"{s['still_produced']}) — {s['description']} [{where}]({where})\n"
                f"  - found as: {s['candidate']}{add}; why: {s['reason']}; "
                f"quote: “{s['evidence_quote']}”"
                f"{'' if s['quote_checked'] else ' (NOT FOUND)'}")  # fmt: skip

    todo = [s for s in suggestions if s["worth_getting"] and not s["covered_by"]]
    for level in ("high", "medium", "low"):
        rows = [s for s in todo if s["priority"] == level]
        if rows:
            out += [f"## {level.title()} priority", "", *map(line, rows), ""]
    covered = [s for s in suggestions if s["covered_by"]]
    if covered:
        out += ["## Already covered", ""]
        out += [f"- {s['candidate']} → {s['covered_by']}" for s in covered] + [""]
    if named:
        out += ["## Named on the site's pages", ""]
        out += [f"- **{n['title']}** ({n['how_to_get']}) — {n['description']} "
                f"[{n['url']}]({n['url']}): “{n['quote']}”" for n in named] + [""]  # fmt: skip
    skipped = [s for s in suggestions if not s["worth_getting"]]
    if skipped:
        out += ["## Reviewed and left out", ""]
        out += [f"- {s['candidate']}: {s['reason']}" for s in skipped] + [""]
    return "\n".join(out)


class Command(BaseCommand):
    help = "Crawl a government's website and suggest record sets to get from it"

    def add_arguments(self, parser):
        parser.add_argument("url", help="the site's home page")
        parser.add_argument("--max-pages", type=int, default=300)
        parser.add_argument("--max-depth", type=int, default=4)
        parser.add_argument("--model", default=None, help="sdllm model (default: a local one)")
        parser.add_argument("--no-review", action="store_true", help="rules only")
        parser.add_argument("--max-candidates", type=int, default=60)

    def handle(self, *args, url, max_pages, max_depth, model, no_review, max_candidates,
               **options):  # fmt: skip
        from sdfetch import Fetcher

        site = site_of(url)
        fetcher = Fetcher(Path(settings.MEDIA_ROOT) / "raw" / "discovery")
        started = time.monotonic()
        pages = crawl(fetcher, url, max_pages=max_pages, max_depth=max_depth)
        found = signals.candidates(pages)
        self.stdout.write(f"{site}: {len(pages)} pages, {len(found)} candidates "
                          f"({time.monotonic() - started:.0f} s)")  # fmt: skip
        suggestions, named = [], []
        for c in found[:max_candidates]:
            base = {"candidate": f"{c.kind}: {c.name}", "covered_by": c.covered_by,
                    "extractor": c.extractor,
                    "urls": c.urls[:5], "files": c.files,
                    "years": [min(c.years), max(c.years)] if c.years else None}  # fmt: skip
            if no_review:
                suggestions.append({**base, "worth_getting": True, "title": c.name,
                                    "description": c.hint, "kind": "records",
                                    "still_produced": "unclear", "how_to_get": "unclear",
                                    "priority": "medium", "reason": "rules only",
                                    "evidence_quote": "", "quote_checked": True})  # fmt: skip
                continue
            suggestions.append({**base, **review.review(c, model=model)})
        if not no_review:
            for page in signals.prose_pages(pages):
                named += review.prose(page, model=model)
        suggestions.sort(key=lambda s: (PRIORITY.get(s["priority"], 3), s["title"]))
        out = Path(settings.MEDIA_ROOT) / "discovery" / site
        out.mkdir(parents=True, exist_ok=True)
        (out / "report.json").write_text(json.dumps(
            {"site": site, "start": url, "pages": len(pages), "suggestions": suggestions,
             "named_on_pages": named, "model": model or "default"}, indent=2))  # fmt: skip
        (out / "report.md").write_text(report_markdown(site, url, len(pages), suggestions, named))
        worth = sum(1 for s in suggestions if s["worth_getting"] and not s["covered_by"])
        self.stdout.write(self.style.SUCCESS(
            f"{worth} suggestions, {len(named)} named on pages "
            f"({time.monotonic() - started:.0f} s): {out / 'report.md'}"))  # fmt: skip
