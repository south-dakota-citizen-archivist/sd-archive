"""The dataset catalog (catalog/datasets.toml): every dataset in the archive and its unit."""

from __future__ import annotations

import tomllib
from functools import cache
from pathlib import Path

from django.conf import settings

STATE = "0400000US46"


def path() -> Path:
    return Path(settings.BASE_DIR).parent / "catalog" / "datasets.toml"


@cache
def datasets() -> dict[str, dict]:
    """Catalog entries by id."""
    return {d["id"]: d for d in tomllib.loads(path().read_text())["dataset"]}


def aggregates_for(place) -> list[dict]:
    """Aggregate datasets that describe places of this kind and cover this one."""
    return sorted(
        (d for d in datasets().values()
         if d["grain"] == "aggregate" and place.kind in d.get("geography", [])
         and (STATE in d.get("coverage", []) or place.geoidfq in d.get("coverage", []))),
        key=lambda d: d["name"],
    )  # fmt: skip
