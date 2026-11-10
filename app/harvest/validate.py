"""Checks that a download looks right before it replaces anything."""

import pandas as pd


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
    """Refuse to replace data when the feature count falls sharply, which
    usually means a broken server or query rather than real deletions."""
    if not previous:
        return {"drop": None}
    drop = (previous - current) / previous
    if drop > max_drop:
        raise ValidationError(
            f"feature count fell from {previous} to {current} ({drop:.0%}), "
            f"more than max_drop={max_drop:.0%}; harvest with force if this is expected"
        )
    return {"drop": round(drop, 4)}
