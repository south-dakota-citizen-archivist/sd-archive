"""pandas helper for pipelines (needs the `pandas` extra)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._places import UnknownPlace, lookup

if TYPE_CHECKING:
    import pandas as pd


def add_geoid(
    df: pd.DataFrame,
    name_column: str,
    *,
    kind: str = "county",
    date_column: str | None = None,
    current: bool = False,
    out_column: str | None = None,
) -> pd.DataFrame:
    """Return a copy of df with a GEOID column for the place named in name_column.

    Every distinct (name, date) is looked up strictly. If any can't be placed, this raises
    UnknownPlace listing all of them at once, rather than leaving blanks to find later.
    date_column: match each row as of its date (a 2010 "Shannon" stays Shannon, 46113).
    current: map to today's places instead (Shannon → Oglala Lakota, 46102).
    """
    out_column = out_column or f"{kind}_geoid"
    names = df[name_column].astype("string")
    dates = df[date_column] if date_column else None
    keys = list(zip(names, dates if dates is not None else [None] * len(df), strict=True))

    resolved: dict[tuple, str] = {}
    problems: list[str] = []
    for name, on in dict.fromkeys(keys):
        as_of = None if on is None else (on.date() if hasattr(on, "date") else on)
        if isinstance(as_of, str):
            as_of = as_of[:10]
        try:
            resolved[(name, on)] = lookup(kind, name, as_of=as_of, current=current).geoid
        except UnknownPlace as e:
            problems.append(str(e))
    if problems:
        raise UnknownPlace(
            f"{len(problems)} value(s) in {name_column!r} can't be placed:\n  "
            + "\n  ".join(problems)
        )

    result = df.copy()
    result[out_column] = [resolved[k] for k in keys]
    return result
