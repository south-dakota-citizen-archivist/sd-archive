"""Checks that a download looks right before it replaces anything."""

import pandas as pd

from records import ingest


class ValidationError(RuntimeError):
    """Raised when downloaded data doesn't look right."""


def validate(df: pd.DataFrame, expected: int, oid_field: str | None) -> dict:
    if len(df) != expected:
        raise ValidationError(f"server reported {expected} features but {len(df)} were downloaded")
    if oid_field and oid_field in df.columns and df[oid_field].duplicated().any():
        dupes = int(df[oid_field].duplicated().sum())
        raise ValidationError(f"{dupes} duplicate {oid_field} values in download")
    return {"count_matches_server": True, "unique_ids": bool(oid_field)}


def check_drop(previous: int | None, current: int, max_drop: float) -> dict:
    """Refuse to replace a layer's features when their count falls sharply (records.ingest)."""
    try:
        return ingest.check_drop(previous, current, max_drop)
    except ingest.DropTooLarge as e:
        raise ValidationError(f"feature {e}") from None
