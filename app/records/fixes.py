"""Hand corrections to what a source publishes, kept in a versioned `fixes.toml` next to the
source, applied in code and recorded on each record they change.

    [[fix]]
    where = {personality_name = "Clyde Fessler Jr."}   # the row it applies to
    field = "form_url"
    published = "https://…/KirkDouglas.PDF"            # what the source says
    corrected = "https://…/ClydeFesslerJr.PDF"
    reason = "The page links Fessler's row to Kirk Douglas's filing."
    checked = 2026-11-10

A fix applies only while the source still publishes the value it corrects; once the source
changes it, the fix is reported as stale and not applied.
"""

from __future__ import annotations

import logging
import tomllib
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Fix:
    where: dict
    field: str
    published: object
    corrected: object
    reason: str
    checked: str = ""


def load(path: str | Path) -> list[Fix]:
    path = Path(path)
    if not path.exists():
        return []
    return [Fix(**{**f, "checked": str(f.get("checked", ""))})
            for f in tomllib.loads(path.read_text()).get("fix", [])]  # fmt: skip


def apply(values: dict, fixes: list[Fix]) -> list[dict]:
    """Correct values in place; return the corrections made, to keep with the record."""
    made = []
    for fix in fixes:
        if any(values.get(k) != v for k, v in fix.where.items()):
            continue
        if values.get(fix.field) != fix.published:
            logger.warning("stale fix for %s %s: the source now has %r", fix.where, fix.field,
                           values.get(fix.field))  # fmt: skip
            continue
        values[fix.field] = fix.corrected
        made.append({"field": fix.field, "published": fix.published,
                     "corrected": fix.corrected, "reason": fix.reason})  # fmt: skip
    return made
