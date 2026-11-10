"""Readable values for display and CSV: code labels and dates. The archive keeps raw values."""

import re

from sdgis.arcgis.client import code_key

# GDAL hands dates over as UTC ISO strings
MIDNIGHT_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})T00:00:00(?:\.0+)?(?:Z|\+00:00)?$")
DATETIME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})(?::\d{2}(?:\.\d+)?)?(Z|\+00:00)?$")


class Labels:
    """Looks up labels for a dataset's coded fields (see client.describe_fields)."""

    def __init__(self, fields: list[dict]):
        # Many domains just repeat the code ("Lake": "Lake"); those labels add nothing
        self.codes = {f["name"]: meaningful(f.get("codes")) for f in fields}
        self.codes = {name: codes for name, codes in self.codes.items() if codes}
        self.by_subtype = {}
        for f in fields:
            by_subtype = {s: meaningful(c) for s, c in (f.get("subtype_codes") or {}).items()}
            if by_subtype := {s: c for s, c in by_subtype.items() if c}:
                self.by_subtype[f["name"]] = by_subtype
        self.subtype_field = next((f["name"] for f in fields if f.get("subtype_field")), None)
        self.dates = {f["name"] for f in fields if f.get("type") == "esriFieldTypeDate"}

    @property
    def coded_fields(self) -> set[str]:
        return set(self.codes) | set(self.by_subtype)

    def label(self, name: str, properties: dict) -> str | None:
        """The label for a field's code, or None if it has no code or the code is unknown.
        A subtype's own labels win over the field's."""
        value = properties.get(name)
        if value is None or name not in self.coded_fields:
            return None
        key = code_key(value)
        if self.subtype_field and name in self.by_subtype:
            subtype = code_key(properties.get(self.subtype_field))
            labels = self.by_subtype[name].get(subtype) or {}
            if key in labels:
                return labels[key]
        return self.codes.get(name, {}).get(key)

    def cell(self, name: str, properties: dict) -> tuple[object, object]:
        """(what to show, the raw code if a label replaced it)."""
        value = properties.get(name)
        label = self.label(name, properties)
        if label is not None:
            return label, value
        if name in self.dates and isinstance(value, str):
            return format_date(value), None
        return value, None


def meaningful(codes: dict | None) -> dict:
    return {code: label for code, label in (codes or {}).items() if label != code}


def format_date(value: str) -> str:
    """2025-11-18T00:00:00.000Z -> 2025-11-18; other times keep hours and minutes."""
    if m := MIDNIGHT_RE.match(value):
        return m[1]
    if m := DATETIME_RE.match(value):
        return f"{m[1]} {m[2]}{' UTC' if m[3] else ''}"
    return value
