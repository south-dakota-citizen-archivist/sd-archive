"""Human review of what the readings of a document leave unsettled: a person looks at the
page and records what it says. The decisions are kept in a versioned TOML file next to the
source that reads the document, and the source applies them on every run, under its own
rules (a part settled by hand; or a hand reading that, like an engine's, still meets the
source's checks, which note anything that doesn't add up).

    [[reading]]
    key = "2022-04"                         # the document (or part of one) the source reads
    field = "table_games/Craps/units"       # what on it
    value = 3                               # left out: the page doesn't give it
    from = "typed"                          # the reading taken ("rapidocr", "ours", …),
                                            # "typed" (none of them right) or "none"
    reason = "Units printed above the row."  # required: what settles the flagged issue
    checked = 2026-11-17

Sources offer what's waiting as items (`Item`, as JSON: a pipeline writes them to a
`review.json` queue beside its build output, with `queue()`), for a review page to show:
the page image, each reading, and the problems the source found. Two kinds:

- `pick`: one field and its readings (a part of a biography).
- `table`: a page's table, every cell with the value the source has and every reading's,
  and the checks that fail (a month's gaming summary).
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import asdict, dataclass
from dataclasses import field as _field
from datetime import date
from pathlib import Path

FROM_TYPED, FROM_NONE = "typed", "none"


class ReviewError(ValueError):
    pass


# --- decisions -------------------------------------------------------------------------


def load(path: str | Path) -> dict[tuple[str, str], dict]:
    """Decisions by (key, field)."""
    path = Path(path)
    if not path.exists():
        return {}
    out = {}
    for d in tomllib.loads(path.read_text()).get("reading", []):
        out[(str(d["key"]), str(d["field"]))] = {**d, "checked": str(d.get("checked", ""))}
    return out


def _toml(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return repr(float(value)) if isinstance(value, float) else str(value)
    return json.dumps(str(value), ensure_ascii=False)  # JSON's string escapes are TOML's too


def _write(path: Path, found: dict[tuple[str, str], dict], header: str) -> None:
    def order(kv):  # natural order: "12" before "2022-04", "9" before "10"
        return [[int(p) if p.isdigit() else p for p in re.split(r"(\d+)", k)] for k in kv[0]]

    blocks = []
    for (key, fld), d in sorted(found.items(), key=order):
        lines = ["[[reading]]", f"key = {_toml(key)}", f"field = {_toml(fld)}"]
        if d.get("value") is not None:
            lines.append(f"value = {_toml(d['value'])}")
        lines += [f"from = {_toml(d['from'])}", f"reason = {_toml(d['reason'])}",
                  f"checked = {d.get('checked') or date.today().isoformat()}"]  # fmt: skip
        blocks.append("\n".join(lines))
    text = "\n\n".join(blocks)
    path.write_text((header.rstrip() + "\n\n" if header else "") + text + ("\n" if text else ""))


def _header(path: Path) -> str:
    if not path.exists():
        return ""
    lines = []
    for line in path.read_text().splitlines():
        if not line.startswith("#"):
            break
        lines.append(line)
    return "\n".join(lines)


def save(path: str | Path, decision: dict, header: str | None = None) -> dict:
    """Add a decision, or replace the one for the same key and field. A reason is
    required. The file keeps its opening comment (or `header`), and decisions in key order."""
    path = Path(path)
    if not (decision.get("reason") or "").strip():
        raise ReviewError("a decision needs a reason: what settles the flagged issue")
    if not decision.get("from"):
        raise ReviewError("a decision needs the reading it's from (or typed, or none)")
    if decision["from"] == FROM_NONE:
        decision = {**decision, "value": None}
    found = load(path)
    d = {**decision, "key": str(decision["key"]), "field": str(decision["field"]),
         "reason": decision["reason"].strip(),
         "checked": decision.get("checked") or date.today().isoformat()}  # fmt: skip
    found[(d["key"], d["field"])] = d
    _write(path, found, _header(path) if header is None else header)
    return d


def remove(path: str | Path, key: str, field_: str) -> bool:
    path = Path(path)
    found = load(path)
    if found.pop((str(key), str(field_)), None) is None:
        return False
    _write(path, found, _header(path))
    return True


def parse_value(text: str, kind: str = "text"):
    """A value typed into a table cell, as its kind: "int" ("1,988"), "money" ("$1,234.56",
    "($3,967.70)" is negative), "pct" ("90.99%"; "N/A" or blank is None) or "text"."""
    t = (text or "").strip()
    if kind == "text":
        return t or None
    if t in ("", "N/A", "#DIV/0!"):
        return None
    negative = t.startswith("(") or t.startswith("-")
    digits = t.strip("()").lstrip("-").replace("$", "").replace(",", "").rstrip("%").strip()
    if not re.fullmatch(r"\d+(\.\d+)?", digits):
        raise ReviewError(f"not a {kind}: {text!r}")
    if kind == "int":
        if "." in digits:
            raise ReviewError(f"not a whole number: {text!r}")
        value = int(digits)
    else:
        value = float(digits)
    return -value if negative else value


# --- items -----------------------------------------------------------------------------


@dataclass
class Item:
    """What's waiting for a person on one page (or one part of a page)."""

    source: str
    key: str  # the document the decisions are keyed by
    kind: str  # "pick" or "table"
    title: str
    subtitle: str = ""
    links: list[dict] = _field(default_factory=list)  # {"label", "url"}
    image: dict = _field(default_factory=dict)  # {"url"} or {"pdf", "pages": [0-based]}
    problems: list[str] = _field(default_factory=list)
    # pick: one field and each reading of it (reader → value)
    field: str = ""
    label: str = ""
    readings: dict = _field(default_factory=dict)
    # table: sections of rows of cells; a cell is {"field", "value", "readings", "kind"}
    sections: list[dict] = _field(default_factory=list)
    context: dict = _field(default_factory=dict)

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, d: dict) -> Item:
        return cls(**d)


def queue(path: str | Path, *, source: str, name: str, decisions: str, items: list[Item],
          check: str | None = None) -> None:  # fmt: skip
    """Write a pipeline's review queue (review.json). `decisions`: the decisions file, and
    `check` (optional): a script that reads an item's table as JSON on stdin and prints the
    problems it finds as a JSON list; both relative to the repository."""
    Path(path).write_text(json.dumps({
        "source": source, "name": name, "decisions": decisions, "check": check,
        "items": [i.to_json() for i in items]}, indent=1, default=str))  # fmt: skip


def read_queue(path: str | Path) -> dict:
    d = json.loads(Path(path).read_text())
    d["items"] = [Item.from_json(i) for i in d["items"]]
    return d
