"""Where review items come from: record sets that offer them (a `REVIEW` provider in a source
package) and pipelines that write a review.json queue when they build
(.cache/<pipeline>/build/review.json, see sddocs.review)."""

from __future__ import annotations

import json
import subprocess
import sys
from importlib import import_module
from pathlib import Path

from django.conf import settings

from sddocs import review

REPO = Path(settings.BASE_DIR).parent
RECORD_PROVIDERS = [
    "records.sources.legislature_blue_book_bios.review",
    "records.newspaper_links",
    "records.sources.parole_board_actions.county_codes",
]


class Provider:
    """A source's items, its decisions file, and what happens once a decision is saved."""

    slug = ""
    name = ""
    decisions: Path
    applies = "on the next run"  # when a decision takes effect

    def items(self) -> list[review.Item]:
        raise NotImplementedError

    def saved(self, key: str) -> None:
        """After decisions for `key` are saved (a record set rebuilds the record)."""

    def check(self, item: review.Item, cells: dict) -> list[str] | None:
        """A table's problems with these cell values (None: the source has no check)."""
        return None

    def decided(self) -> dict[tuple[str, str], dict]:
        return review.load(self.decisions)


class PipelineProvider(Provider):
    """A pipeline's queue, as its last build wrote it. Its check script is run with the
    app's Python, so it should need only the standard library."""

    def __init__(self, path: Path):
        self.path = path
        q = review.read_queue(path)
        self.slug, self.name, self._items = q["source"], q["name"], q["items"]
        self.decisions = REPO / q["decisions"]
        self.check_script = REPO / q["check"] if q.get("check") else None
        self.applies = f"when the pipeline is built again (`just {path.parents[1].name}`)"

    def items(self) -> list[review.Item]:
        return self._items

    def check(self, item: review.Item, cells: dict) -> list[str] | None:
        if not self.check_script:
            return None
        done = subprocess.run([sys.executable, str(self.check_script)],
                              input=json.dumps({"key": item.key, "cells": cells}),
                              capture_output=True, text=True, cwd=REPO, timeout=60)  # fmt: skip
        if done.returncode:
            return [f"the check didn't run: {done.stderr.strip().splitlines()[-1:]}"]
        return json.loads(done.stdout)


def providers() -> dict[str, Provider]:
    out: dict[str, Provider] = {}
    for module in RECORD_PROVIDERS:
        p = import_module(module).REVIEW
        out[p.slug] = p
    for path in sorted((REPO / ".cache").glob("*/build/review.json")):
        p = PipelineProvider(path)
        out[p.slug] = p
    return out


def pdf_path(relative: str) -> Path:
    """A pipeline's stored document, only from the repository's .cache."""
    path = (REPO / relative).resolve()
    if not path.is_relative_to((REPO / ".cache").resolve()) or path.suffix.lower() != ".pdf":
        raise ValueError("not a stored document")
    return path
