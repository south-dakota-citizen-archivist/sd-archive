"""The disk guard: big downloads and OCR runs don't start when the disk is nearly full."""

from __future__ import annotations

import shutil
from pathlib import Path

DEFAULT_GB = 50


class DiskSpaceLow(OSError):
    pass


def free_gb(path: str | Path) -> float:
    """Free space, in GB, on the disk holding path (or its nearest existing parent)."""
    p = Path(path).expanduser().absolute()
    while not p.exists():
        p = p.parent
    return shutil.disk_usage(p).free / 1e9


def ensure_free(path: str | Path, gb: float = DEFAULT_GB) -> float:
    """Raise DiskSpaceLow, saying how much is free, when less than gb GB is free at path."""
    free = free_gb(path)
    if free < gb:
        raise DiskSpaceLow(
            f"only {free:.0f} GB free on the disk holding {path}; this needs at least {gb:g} GB"
        )
    return free
