"""One Chrome window on a Beacon app, reading it one request at a time.

The window is Google Chrome (patchright) with a browser profile kept between runs. The map
page is opened, its terms and conditions accepted with a click on "Agree" (and any notice
after them closed), and then:

- api(method, body): the map's API call, sent by the map page itself (same origin, cookies,
  headers and session key as the map's own scripts)
- report(key): a parcel's report page, opened by navigating a second tab to it

Between requests it waits a few seconds (jittered), and now and then for a few minutes. When
the site refuses a request (HTTP 403, 429 or 503, or an interstitial page that doesn't go
away), it waits a long while and reopens the map; after `max_blocks` refusals in a row it
stops (Blocked), keeping everything already fetched.
"""

from __future__ import annotations

import logging
import random
import time
from contextlib import suppress
from pathlib import Path

from records.sources._beacon import BASE_URL, parse

logger = logging.getLogger(__name__)

CHALLENGE_TITLES = ("just a moment", "attention required", "access denied", "verify you are")
BLOCK_STATUSES = {403, 429, 503}
_API = """async ([method, body]) => {
  const r = await fetch('/api/beaconCore/' + method + '?QPS=' + encodeURIComponent(mapConfig.QPS), {
    method: 'POST',
    headers: {'content-type': 'application/json', 'x-requested-with': 'XMLHttpRequest',
              'accept': 'text/plain, */*; q=0.01'},
    body: JSON.stringify(body)});
  return [r.status, await r.text()];
}"""


class Blocked(Exception):
    """The site kept answering with block pages."""


class Session:
    def __init__(self, app: str, profile: str | Path, *, headless: bool = False,
                 pace: tuple[float, float] = (3.0, 8.0),
                 report_pace: tuple[float, float] = (7.0, 18.0),
                 rest_every: tuple[int, int] = (40, 90),
                 rest: tuple[float, float] = (90.0, 300.0),
                 backoff: float = 900.0, max_blocks: int = 4,
                 sleep=time.sleep, rng: random.Random | None = None):  # fmt: skip
        self.app, self.profile, self.headless = app, Path(profile), headless
        self.pace, self.report_pace = pace, report_pace
        self.rest_every, self.rest = rest_every, rest
        self.backoff, self.max_blocks = backoff, max_blocks
        self.sleep, self.rng = sleep, rng or random.Random()
        self.config: dict = {}
        self.requests = 0
        self._next_rest = self.rng.randint(*rest_every)
        self._blocks = 0
        self._pw = self._ctx = self.map = self.tab = None

    @property
    def map_url(self) -> str:
        return f"{BASE_URL}/Application.aspx?App={self.app}&PageType=Map"

    # --- the browser ---

    def __enter__(self) -> Session:
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def start(self) -> None:
        from patchright.sync_api import sync_playwright

        self.profile.mkdir(parents=True, exist_ok=True)
        self._pw = sync_playwright().start()
        self._ctx = self._pw.chromium.launch_persistent_context(
            str(self.profile), channel="chrome", headless=self.headless, no_viewport=True,
            locale="en-US", args=["--window-size=1280,900"],
        )  # fmt: skip
        self.map = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
        self.open_map()

    def close(self) -> None:
        for close in (getattr(self._ctx, "close", None), getattr(self._pw, "stop", None)):
            if close:
                with suppress(Exception):  # the window was closed by hand
                    close()
        self._ctx = self._pw = None

    def _cleared(self, page, wait: float = 60.0) -> bool:
        """Wait for an interstitial page to give way to the page; True once it has."""
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            try:
                title = page.title().lower()
            except Exception:
                title = ""
            if not any(t in title for t in CHALLENGE_TITLES):
                return True
            self.sleep(2)
        return False

    def open_map(self) -> None:
        """Open (or reopen) the map page and read its configuration."""
        response = self.map.goto(self.map_url, wait_until="domcontentloaded", timeout=90000)
        if not self._cleared(self.map) or (response and response.status in BLOCK_STATUSES
                                           and not self._has_map()):  # fmt: skip
            raise _BlockPage(f"map page: HTTP {response.status if response else '?'}")
        self.map.wait_for_function("typeof mapConfig !== 'undefined'", timeout=90000)
        self.config = parse.map_config(self.map.content())
        self.accept_terms(self.map)
        self.sleep(self.rng.uniform(4, 9))  # the map loads its layers and tiles

    def accept_terms(self, page, wait: float = 12.0) -> bool:
        """Click "Agree" on the terms and conditions dialog if it's showing (after a pause to
        read it), then "Close" on any notices that follow. True if it agreed."""
        agree = page.get_by_role("button", name="Agree", exact=True).first
        try:
            agree.wait_for(state="visible", timeout=wait * 1000)
        except Exception:
            return False
        self.sleep(self.rng.uniform(2.0, 5.0))
        agree.click()
        logger.info("beacon %s: accepted the terms", self.app)
        for _ in range(5):  # notices, one after another
            close = page.get_by_role("button", name="Close", exact=True).first
            try:
                close.wait_for(state="visible", timeout=3000)
            except Exception:
                break
            self.sleep(self.rng.uniform(1.0, 3.0))
            close.click()
        return True

    def _has_map(self) -> bool:
        try:
            return bool(self.map.evaluate("typeof mapConfig !== 'undefined'",
                                          isolated_context=False))  # fmt: skip
        except Exception:
            return False

    # --- pacing and blocks ---

    def _wait(self, pace: tuple[float, float]) -> None:
        self.requests += 1
        if self.requests >= self._next_rest:
            pause = self.rng.uniform(*self.rest)
            logger.info("beacon %s: resting %.0f s after %d requests", self.app, pause,
                        self.requests)  # fmt: skip
            self.sleep(pause)
            self._next_rest = self.requests + self.rng.randint(*self.rest_every)
        else:
            self.sleep(self.rng.uniform(*pace))

    def _blocked(self, what: str) -> None:
        self._blocks += 1
        if self._blocks > self.max_blocks:
            raise Blocked(f"{self.app}: blocked {self._blocks} times in a row ({what})")
        pause = self.backoff * 2 ** (self._blocks - 1) * self.rng.uniform(0.8, 1.3)
        logger.warning("beacon %s: blocked (%s); waiting %.0f min", self.app, what, pause / 60)
        self.sleep(pause)
        with suppress(_BlockPage):  # counted on the next try
            self.open_map()

    def _retrying(self, attempt):
        while True:
            try:
                result = attempt()
            except _BlockPage as e:
                self._blocked(str(e))
                continue
            self._blocks = 0
            return result

    # --- requests ---

    def api(self, method: str, body: dict):
        """A map API call from the map page; its answer's "d"."""

        def attempt():
            self._wait(self.pace)
            status, text = self.map.evaluate(_API, [method, body], isolated_context=False)
            if status in BLOCK_STATUSES or _is_challenge(text):
                raise _BlockPage(f"{method}: HTTP {status}")
            if status != 200:
                raise RuntimeError(f"{self.app} {method}: HTTP {status}")
            import json

            return json.loads(text)["d"]

        return self._retrying(attempt)

    def report(self, key: str) -> bytes:
        """A parcel's report page, opened in the second tab."""
        url = parse.report_url(self.config, key)

        def attempt():
            self._wait(self.report_pace)
            new_tab = self.tab is None or self.tab.is_closed()
            if new_tab:
                self.tab = self._ctx.new_page()
            response = self.tab.goto(url, wait_until="domcontentloaded", timeout=90000)
            if not self._cleared(self.tab):
                raise _BlockPage(f"report {key}: interstitial page didn't go away")
            # the terms come up once per tab (or once per profile, depending on the app)
            self.accept_terms(self.tab, wait=12.0 if new_tab else 2.0)
            html = self.tab.content()
            status = response.status if response else 0
            if "module-header" not in html:
                if status in BLOCK_STATUSES or _is_challenge(html):
                    raise _BlockPage(f"report {key}: HTTP {status}")
                raise RuntimeError(f"{self.app} report {key}: HTTP {status}, not a report")
            return html.encode()

        return self._retrying(attempt)


class _BlockPage(Exception):
    pass


def _is_challenge(text: str) -> bool:
    head = text[:5000].lower()
    return any(t in head for t in CHALLENGE_TITLES) or "cf-challenge" in head or (
        "challenge-platform" in head and len(text) < 60000)  # fmt: skip
