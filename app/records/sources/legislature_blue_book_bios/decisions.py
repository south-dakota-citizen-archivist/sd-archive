"""Parts of a Blue Book page settled by hand: where the three readings don't agree, someone
looks at the page image and records what it says. Kept in a versioned `fixes.toml` next to
the source and applied on every run.

    [[reading]]
    image = "3389"                        # the page image's ID (the record's key)
    part = "counties"
    value = "part of Pennington County"   # left out: the part is left empty
    from = "ours"                         # the reading chosen: legislature, ours, vision; or
                                          # typed (none of them right); or none
    reason = "Checked against the page image."
    checked = 2026-11-16

A decision applies only while its part is unconfirmed; once two readings agree on the part,
the readings are used and the decision is reported as unused.
"""

from __future__ import annotations

import json
import tomllib
from datetime import date
from pathlib import Path

PATH = Path(__file__).with_name("fixes.toml")
FROM = ("legislature", "ours", "vision", "typed", "none")
HEADER = """\
# Parts of Blue Book pages settled by hand against the page image, where the three readings
# don't agree (see decisions.py). Written by the review page; can be edited by hand.
"""


def load(path: Path | None = None) -> dict[tuple[str, str], dict]:
    """Decisions by (image, part)."""
    path = path or PATH
    if not path.exists():
        return {}
    out = {}
    for d in tomllib.loads(path.read_text()).get("reading", []):
        out[(str(d["image"]), d["part"])] = {**d, "checked": str(d.get("checked", ""))}
    return out


def _toml(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)  # JSON's string escapes are TOML's too


def save(decision: dict, path: Path | None = None) -> None:
    """Add a decision, or replace the one for the same image and part; the file is kept in
    image order."""
    path = path or PATH
    if decision["from"] not in FROM:
        raise ValueError(f"unknown reading: {decision['from']}")
    found = load(path)
    found[(str(decision["image"]), decision["part"])] = {
        **decision, "checked": decision.get("checked") or date.today().isoformat()}  # fmt: skip
    blocks = []
    for (image, part), d in sorted(found.items(), key=lambda kv: (int(kv[0][0]), kv[0][1])):
        lines = ["[[reading]]", f"image = {_toml(image)}", f"part = {_toml(part)}"]
        if d.get("value") is not None:
            lines.append(f"value = {_toml(d['value'])}")
        lines += [f"from = {_toml(d['from'])}", f"reason = {_toml(d.get('reason') or '')}",
                  f"checked = {d['checked'] or date.today().isoformat()}"]  # fmt: skip
        blocks.append("\n".join(lines))
    path.write_text(HEADER + "\n" + "\n\n".join(blocks) + "\n")
