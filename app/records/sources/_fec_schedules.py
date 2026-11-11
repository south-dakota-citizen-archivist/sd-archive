"""Schedules A and B for South Dakota's committees, committee by committee and cycle by
cycle (the API times out on several committees at once). Past cycles are fetched once and
kept; the current cycle is fetched again each month.

A run speaks for the committees and cycles it read (covering): a stored transaction of one
of those that the FEC no longer lists (an amended report replaces its transactions) is
marked dropped; one of a committee or cycle the run didn't read is left alone."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

from records.sources import _fec


def current_cycle(today: date | None = None) -> int:
    year = (today or date.today()).year
    return year + year % 2


def committees() -> list[tuple[str, list[int]]]:
    """(committee ID, cycles) for South Dakota's committees, from the fec-committees records."""
    from records.models import Record

    out = []
    rows = Record.objects.current().filter(recordset__slug="fec-committees")
    for data in rows.values_list("data", flat=True):
        cycles = sorted(c for c in data.get("cycles") or [] if c >= _fec.SINCE_CYCLE)
        out.append((data["committee_id"], cycles))
    return sorted(out)


def schedule(fetcher, path: str, label: str, sort: str, today: date | None = None,
             read: set | None = None):  # fmt: skip
    """Every item of a schedule for every South Dakota committee and cycle; each (committee,
    cycle) read to the end is added to `read`."""
    month = (today or date.today()).strftime("%Y-%m")
    now = current_cycle(today)
    for committee_id, cycles in committees():
        for cycle in cycles:
            name = f"{label}/{committee_id}/{cycle}" + (f"/{month}" if cycle >= now else "")
            params = {"committee_id": committee_id, "two_year_transaction_period": cycle,
                      "sort": sort}  # fmt: skip
            yield from _fec.cursor(fetcher, path, params, name)
            if read is not None:
                read.add((committee_id, cycle))


def covering(slug: str, read: set) -> Callable[[str], bool]:
    """Whether a stored transaction (by key) is of a committee and cycle the run read."""
    from records.models import Record

    where: dict[str, tuple] = {}

    def covers(key: str) -> bool:
        if not where:
            rows = Record.objects.filter(recordset__slug=slug).values_list(
                "key", "data__committee_id", "data__two_year_transaction_period")  # fmt: skip
            where.update((k, (c, y)) for k, c, y in rows.iterator())
        return where.get(key) in read

    return covers
