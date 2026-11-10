"""Checkpoints: which units of work are finished, written down after each one."""

from __future__ import annotations

import json
from pathlib import Path

from ._store import now


class Checkpoint:
    """A JSON file of finished units (by key) and a cursor.

    Each mark() rewrites the file atomically, so an interruption loses at most the unit in
    progress, and the next run skips what's done:

        cp = checkpoint("checkpoint.json")
        for unit in units:
            if cp.done(unit):
                continue
            work(unit)
            cp.mark(unit, rows=12)
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        data = json.loads(self.path.read_text()) if self.path.exists() else {}
        self._done: dict[str, dict] = data.get("done", {})
        self._cursor = data.get("cursor")

    def __len__(self) -> int:
        return len(self._done)

    def __repr__(self) -> str:
        return f"Checkpoint({str(self.path)!r}, done={len(self)}, cursor={self._cursor!r})"

    def done(self, key) -> bool:
        return str(key) in self._done

    def info(self, key) -> dict | None:
        """What was recorded when the unit was marked done."""
        return self._done.get(str(key))

    def mark(self, key, **info) -> None:
        self._done[str(key)] = {**info, "at": now()}
        self._save()

    def unmark(self, key) -> None:
        """Do the unit again next run."""
        if self._done.pop(str(key), None) is not None:
            self._save()

    @property
    def cursor(self):
        """Where a paged or incremental source got to (a page, an ID, a date)."""
        return self._cursor

    @cursor.setter
    def cursor(self, value) -> None:
        self._cursor = value
        self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".partial")
        data = {"cursor": self._cursor, "updated_at": now(), "done": self._done}
        tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False, default=str))
        tmp.replace(self.path)


def checkpoint(path: str | Path) -> Checkpoint:
    return Checkpoint(path)
