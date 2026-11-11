"""The FEC's API (api.open.fec.gov), for the FEC record sets: paged lists (candidates,
committees) and cursor-paged schedules (A receipts, B disbursements, E independent
expenditures). Uses settings.FEC_API_KEY (an api.data.gov key), else FEC's DEMO_KEY."""

from __future__ import annotations

import json
from collections.abc import Iterator

API = "https://api.open.fec.gov/v1"
WEB = "https://www.fec.gov/data"
STATE = "0400000US46"
PER_PAGE = 100
SINCE_CYCLE = 2008  # the API's itemized schedules are reliable from here


def api_key() -> str:
    from django.conf import settings

    return getattr(settings, "FEC_API_KEY", "") or "DEMO_KEY"


def _get(fetcher, path: str, params: dict, name: str | None) -> dict:
    query = {**params, "api_key": api_key(), "per_page": PER_PAGE}
    if name and getattr(fetcher, "store", None) is not None:
        return json.loads(fetcher.fetch(f"{API}{path}", name, params=query))
    return fetcher.request("GET", f"{API}{path}", params=query).json()


def pages(fetcher, path: str, params: dict, name: str = "") -> Iterator[dict]:
    """Every result of a page-numbered list."""
    page = 1
    while True:
        data = _get(fetcher, path, {**params, "page": page},
                    f"{name}/page-{page:04d}.json" if name else None)  # fmt: skip
        yield from data.get("results") or []
        if page >= ((data.get("pagination") or {}).get("pages") or 1):
            return
        page += 1


def cursor(fetcher, path: str, params: dict, name: str = "") -> Iterator[dict]:
    """Every result of a schedule, following last_indexes."""
    after: dict = {}
    n = 0
    while True:
        n += 1
        data = _get(fetcher, path, {**params, **after},
                    f"{name}/page-{n:04d}.json" if name else None)  # fmt: skip
        results = data.get("results") or []
        yield from results
        last = (data.get("pagination") or {}).get("last_indexes") or {}
        if not results or not last.get("last_index"):
            return
        after = {k: v for k, v in last.items() if v is not None}
