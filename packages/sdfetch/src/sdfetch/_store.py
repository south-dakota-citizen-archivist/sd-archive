"""A folder of raw files, each saved whole or not at all, and a manifest of their sources."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from ._redact import redact


class EmptyResponse(Exception):
    """A refresh came back empty, so the good copy already stored was kept."""


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Store:
    """Raw files under `root`, by name ("pages/default_eid=3.html"), plus `manifest.jsonl`.

    Every save appends a manifest line: name, URL, method, status, bytes, SHA-256, time, and
    whatever note the caller adds. A file is written to `<name>.partial` and renamed, so an
    interruption never leaves half a file. `blob()` stores by content instead, at
    `blobs/ab/cd/<sha256><suffix>`, for documents whose names mean nothing.
    """

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def __repr__(self) -> str:
        return f"Store({str(self.root)!r})"

    def path(self, name: str) -> Path:
        p = PurePosixPath(name)
        if p.is_absolute() or ".." in p.parts or not p.parts:
            raise ValueError(f"store names are relative paths inside the store: {name!r}")
        return self.root / p

    def has(self, name: str) -> bool:
        return self.path(name).exists()

    def read(self, name: str) -> bytes:
        return self.path(name).read_bytes()

    def save(
        self,
        name: str,
        content: bytes,
        url: str = "",
        *,
        method: str = "GET",
        status: int | None = None,
        note: dict | None = None,
        allow_empty: bool = False,
    ) -> Path:
        """Write content at name and record it. An empty body doesn't replace a stored one
        (EmptyResponse) unless allow_empty."""
        path = self.path(name)
        if not content and not allow_empty and path.exists() and path.stat().st_size:
            raise EmptyResponse(
                f"{redact(url) if url else name}: empty response; kept the stored copy"
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".partial")
        tmp.write_bytes(content)
        tmp.replace(path)
        self._record(name, content, url, method, status, note)
        return path

    def blob(
        self,
        content: bytes,
        url: str = "",
        *,
        suffix: str = "",
        method: str = "GET",
        status: int | None = None,
        note: dict | None = None,
    ) -> str:
        """Store content by its SHA-256 and return its name. The same bytes are stored once,
        but each fetch of them is recorded."""
        sha = hashlib.sha256(content).hexdigest()
        name = f"blobs/{sha[:2]}/{sha[2:4]}/{sha}{suffix}"
        path = self.path(name)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".partial")
            tmp.write_bytes(content)
            tmp.replace(path)
        self._record(name, content, url, method, status, note)
        return name

    def manifest(self) -> list[dict]:
        path = self.root / "manifest.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    def _record(self, name, content, url, method, status, note) -> None:
        entry = {
            "url": redact(url),
            "method": method,
            **({"status": status} if status is not None else {}),
            **(note or {}),
            "path": name,
            "bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
            "fetched_at": now(),
        }
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / "manifest.jsonl").open("a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
