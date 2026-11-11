"""A stand-in for the FEC's API in tests: answers each path from fixture files."""

from __future__ import annotations

import json
from pathlib import Path


class FixtureFetcher:
    """`routes` maps an API path ("/candidates/") to fixture files, served in order: the
    first page, then the next one asked for (by page number or cursor)."""

    store = None

    def __init__(self, routes: dict[str, list[Path]]):
        self.routes, self.asked = routes, []

    def request(self, method, url, params=None, **kwargs):
        path = url.split("/v1", 1)[1]
        self.asked.append((path, dict(params or {})))
        files = self.routes[path]
        n = sum(1 for p, _ in self.asked if p == path) - 1

        class Resp:
            def json(self):
                return json.loads(files[min(n, len(files) - 1)].read_text())

        return Resp()
