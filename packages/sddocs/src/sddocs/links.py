"""Links to the page a value was read from."""

from __future__ import annotations

import math


def page_url(url: str | None, page) -> str | None:
    """A PDF's link opened at a page (#page=, which browsers' PDF viewers follow). Other
    links, and rows without a page, are returned as they are."""
    if not url or page is None or (isinstance(page, float) and math.isnan(page)):
        return url
    base = url.split("#", 1)[0]
    if not base.split("?", 1)[0].lower().endswith(".pdf"):
        return url
    return f"{base}#page={int(page)}"


def with_page_links(df):
    """A table whose rows have a source_url and a page, with each link opened at its page."""
    if "source_url" not in df.columns or "page" not in df.columns:
        return df
    df = df.copy()
    df["source_url"] = [page_url(u, p) for u, p in zip(df["source_url"], df["page"], strict=True)]
    return df
