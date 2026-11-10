import random
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

import sdfetch
from sdfetch._browser import BrowserResponse
from sdfetch._pacing import Budget, BudgetSpent, OutsideHours, check_window, jittered, parse_days

CHICAGO = ZoneInfo("America/Chicago")
URL = "https://records.example.gov/search"


def at(*args):
    return datetime(*args, tzinfo=CHICAGO)


def test_jitter_stays_in_range():
    rng = random.Random(1)
    gaps = [jittered(10, 0.4, rng) for _ in range(500)]
    assert min(gaps) >= 6 and max(gaps) <= 14 and len({round(g, 3) for g in gaps}) > 400
    assert jittered(10, 0, rng) == 10


def test_parse_days():
    assert parse_days("mon-fri") == {0, 1, 2, 3, 4}
    assert parse_days("sat,sun") == {5, 6}
    assert parse_days("fri-mon") == {4, 5, 6, 0}
    assert parse_days("Mon, Wed-Thu") == {0, 2, 3}


def test_window():
    check_window(at(2026, 11, 10, 9, 30), "08:00-18:00", "mon-fri", "America/Chicago")  # Tue
    with pytest.raises(OutsideHours) as e:
        check_window(at(2026, 11, 10, 19, 0), "08:00-18:00", "mon-fri", "America/Chicago")
    assert e.value.until == at(2026, 11, 11, 8, 0)
    with pytest.raises(OutsideHours) as e:  # Saturday morning: next opening is Monday
        check_window(at(2026, 11, 14, 9, 0), "08:00-18:00", "mon-fri", "America/Chicago")
    assert e.value.until == at(2026, 11, 16, 8, 0)
    # The window is in its own time zone, whatever the clock's
    check_window(datetime(2026, 11, 10, 15, 0, tzinfo=UTC), "08:00-18:00", None, "America/Chicago")


def test_overnight_window_belongs_to_its_start_day():
    check_window(at(2026, 11, 13, 23, 0), "22:00-06:00", "mon-fri", "America/Chicago")  # Fri
    check_window(at(2026, 11, 14, 5, 0), "22:00-06:00", "mon-fri", "America/Chicago")  # Fri night
    with pytest.raises(OutsideHours) as e:  # Saturday night isn't in the window
        check_window(at(2026, 11, 14, 23, 0), "22:00-06:00", "mon-fri", "America/Chicago")
    assert e.value.until == at(2026, 11, 16, 22, 0)


def test_budget_holds_across_runs(tmp_path):
    day = date(2026, 11, 10)
    Budget(tmp_path / "b.json").spend("x.gov", 2, day)
    again = Budget(tmp_path / "b.json")  # a later run
    assert again.spend("x.gov", 2, day) == 2
    with pytest.raises(BudgetSpent) as e:
        again.spend("x.gov", 2, day)
    assert e.value.until.date() == day + timedelta(days=1)
    assert again.spend("y.gov", 2, day) == 1  # per host
    assert again.spend("x.gov", 2, day + timedelta(days=1)) == 1  # a new day


def fetcher(tmp_path, calls, **kwargs):
    def send(method, url, **kw):
        calls.append(url)
        return BrowserResponse(url, 200, b"ok", {})

    return sdfetch.Fetcher(tmp_path, memory=False, delay=0, clients={"http": send},
                           budget_file=tmp_path / "budget.json", sleep=lambda s: None,
                           **kwargs)  # fmt: skip


def test_fetcher_respects_hours_before_sending(tmp_path):
    calls = []
    f = fetcher(tmp_path, calls, hours="08:00-18:00", tz="America/Chicago",
                clock=lambda: at(2026, 11, 10, 20, 0))  # fmt: skip
    with pytest.raises(sdfetch.OutsideHours) as e:
        f.request("GET", URL)
    assert calls == [] and isinstance(e.value, sdfetch.NotNow)


def test_fetcher_budget_from_config(tmp_path):
    calls = []
    config = {"hosts": {"example.gov": {"budget": 2, "jitter": 0.5}}}
    f = fetcher(tmp_path, calls, config=config)
    f.request("GET", URL)
    f.request("GET", URL)
    with pytest.raises(sdfetch.BudgetSpent):
        f.request("GET", URL)
    assert len(calls) == 2
    f.request("GET", "https://other.example.org/")  # other hosts aren't limited


def test_jitter_in_the_pause(tmp_path):
    waits = []
    sdfetch._http._host_next.clear()
    f = sdfetch.Fetcher(tmp_path, memory=False, delay=10, jitter=0.5, rng=random.Random(3),
                        clients={"http": lambda m, u, **k: BrowserResponse(u, 200, b"", {})},
                        sleep=waits.append)  # fmt: skip
    for _ in range(4):
        f.request("GET", "https://jitter.example.org/")
    # The fake sleep doesn't move the clock, so each wait includes the ones before it
    gaps = [b - a for a, b in zip([0.0, *waits], waits, strict=False)]
    assert len(gaps) == 3 and all(4.9 < g < 15.1 for g in gaps)
    assert len({round(g, 2) for g in gaps}) == 3
