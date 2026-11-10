"""Pacing: jittered pauses, hours of the day, and a daily request budget per host."""

from __future__ import annotations

import json
import random
import threading
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from ._ladder import cache_dir

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


class NotNow(Exception):
    """Not a failure: try again at `until` (outside hours, or the day's budget is spent)."""

    def __init__(self, message: str, until: datetime | None = None):
        super().__init__(message)
        self.until = until


class OutsideHours(NotNow):
    pass


class BudgetSpent(NotNow):
    pass


def jittered(gap: float, jitter: float, rng: random.Random) -> float:
    """gap, spread evenly by ±jitter (0.3 → 70% to 130% of it)."""
    if not jitter or gap <= 0:
        return gap
    return max(0.0, gap * rng.uniform(1 - jitter, 1 + jitter))


def parse_hours(text: str) -> tuple[time, time]:
    """ "08:00-18:00" (or overnight, "22:00-06:00")."""
    start, end = (time.fromisoformat(t.strip()) for t in text.split("-"))
    return start, end


def parse_days(text: str) -> set[int]:
    """ "mon-fri", "sat,sun" or "mon,wed-fri" → weekday numbers (Monday is 0)."""
    out: set[int] = set()
    for part in text.lower().replace(" ", "").split(","):
        if "-" in part:
            a, b = (DAYS.index(d[:3]) for d in part.split("-"))
            out |= {d % 7 for d in range(a, a + (b - a) % 7 + 1)}
        elif part:
            out.add(DAYS.index(part[:3]))
    return out


def _open_at(moment: datetime, hours: tuple[time, time] | None, days: set[int] | None) -> bool:
    """Whether moment is inside the window. An overnight window ("22:00-06:00") belongs to
    the day it starts on."""
    if hours is None:
        return days is None or moment.weekday() in days
    start, end, t = hours[0], hours[1], moment.time()
    if start < end:
        inside, day = start <= t < end, moment.weekday()
    elif t >= start:
        inside, day = True, moment.weekday()
    elif t < end:
        inside, day = True, (moment.weekday() - 1) % 7
    else:
        inside, day = False, moment.weekday()
    return inside and (days is None or day in days)


def check_window(now: datetime, hours: str | None, days: str | None, tz: str | None = None) -> None:
    """Raise OutsideHours, with when the window next opens, if now is outside it. hours are
    in tz (an IANA name such as "America/Chicago"), or this machine's time zone."""
    if not hours and not days:
        return
    moment = now.astimezone(ZoneInfo(tz)) if tz else now.astimezone()
    h = parse_hours(hours) if hours else None
    d = parse_days(days) if days else None
    if _open_at(moment, h, d):
        return
    label = " ".join(x for x in (hours, days) if x)
    probe = moment.replace(second=0, microsecond=0)
    for _ in range(8 * 24 * 60):  # a minute at a time, up to eight days ahead
        probe += timedelta(minutes=1)
        if _open_at(probe, h, d):
            raise OutsideHours(f"outside {label}", until=probe)
    raise OutsideHours(f"outside {label}")


class Budget:
    """Requests per host per day, counted in budget.json so the count holds across runs."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else cache_dir() / "budget.json"
        self._lock = threading.Lock()

    def _load(self) -> dict:
        return json.loads(self.path.read_text()) if self.path.exists() else {}

    def used(self, host: str, day: date | None = None) -> int:
        return self._load().get((day or date.today()).isoformat(), {}).get(host, 0)

    def spend(self, host: str, limit: int, day: date | None = None) -> int:
        """Count one request, or raise BudgetSpent if the day's limit is reached."""
        day = day or date.today()
        with self._lock:
            data = self._load()
            today = data.get(day.isoformat(), {})
            if today.get(host, 0) >= limit:
                tomorrow = datetime.combine(day + timedelta(days=1), time()).astimezone()
                raise BudgetSpent(f"{host}: {limit} requests today", until=tomorrow)
            today[host] = today.get(host, 0) + 1
            # Keep the last week only
            data = {k: v for k, v in data.items() if k >= (day - timedelta(days=7)).isoformat()}
            data[day.isoformat()] = today
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".partial")
            tmp.write_text(json.dumps(data, indent=1, sort_keys=True))
            tmp.replace(self.path)
            return today[host]
