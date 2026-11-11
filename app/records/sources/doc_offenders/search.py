"""The locator's search, in a Chrome window: a last name typed in, the "I'm not a robot" box
ticked, the search submitted. The results' later pages are plain form posts (parse.next_page)
and the people's pages plain links, so only the first page of each search needs the window.

If the box asks for more than a tick (a picture puzzle), the search stops (NeedsPerson) and
the run keeps what it has; the next run starts with the next search.
"""

from __future__ import annotations

import random
import time
from contextlib import suppress
from pathlib import Path

from . import parse


class NeedsPerson(Exception):
    """The "I'm not a robot" box asked for more than a tick."""


class Searcher:
    def __init__(self, profile: str | Path, *, headless: bool = False, sleep=time.sleep,
                 rng: random.Random | None = None):  # fmt: skip
        self.profile, self.headless, self.sleep = Path(profile), headless, sleep
        self.rng = rng or random.Random()
        self._pw = self._ctx = self.page = None

    def __enter__(self) -> Searcher:
        from patchright.sync_api import sync_playwright

        self.profile.mkdir(parents=True, exist_ok=True)
        self._pw = sync_playwright().start()
        self._ctx = self._pw.chromium.launch_persistent_context(
            str(self.profile), channel="chrome", headless=self.headless, no_viewport=True,
            locale="en-US", args=["--window-size=1280,900"],
        )  # fmt: skip
        self.page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
        return self

    def __exit__(self, *exc) -> None:
        for close in (getattr(self._ctx, "close", None), getattr(self._pw, "stop", None)):
            if close:
                with suppress(Exception):
                    close()

    def search(self, last_name: str) -> bytes:
        """The first page of results for a last name (a prefix: "Z" finds every Z name)."""
        page = self.page
        page.goto(parse.URL, wait_until="domcontentloaded", timeout=90000)
        self.sleep(self.rng.uniform(2.5, 5.0))
        page.locator("#txtLName").click()
        page.keyboard.type(last_name, delay=self.rng.uniform(80, 180))
        self.sleep(self.rng.uniform(0.8, 2.0))
        box = page.frame_locator("iframe[title='reCAPTCHA']").locator("#recaptcha-anchor")
        box.click()
        for _ in range(30):
            self.sleep(0.5)
            if box.get_attribute("aria-checked") == "true":
                break
            puzzle = page.locator("iframe[title*='challenge']")
            if puzzle.count() and puzzle.first.is_visible():
                raise NeedsPerson(f"search {last_name!r}: the box asked for a picture puzzle")
        else:
            raise NeedsPerson(f"search {last_name!r}: the box wasn't ticked")
        self.sleep(self.rng.uniform(0.8, 2.0))
        with page.expect_navigation(wait_until="domcontentloaded", timeout=90000):
            page.locator("input[name=btnMission]").click()
        self.sleep(self.rng.uniform(1.0, 2.5))
        return page.content().encode()
