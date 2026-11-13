"""The locator's search, in a Chrome window: a last name typed in, the "I'm not a robot" box
ticked, the search submitted. The results' later pages are plain form posts (parse.next_page)
and the people's pages plain links, so only the first page of each search needs the window.

The window is used the way a person uses it: the mouse travels in a curve to what it clicks
and rests on it a moment first, the name is typed a key at a time at an uneven pace, and each
page is looked over (a pause, a scroll) before anything is done on it. The first search of a
session starts after a minute or so on the page.

If the box asks for more than a tick (a picture puzzle), the window waits up to
`PERSON_WAIT` seconds for a person to solve it; if nobody does, the search stops
(NeedsPerson) and the run keeps what it has, and the next run starts with the next search.
"""

from __future__ import annotations

import logging
import random
import time
from contextlib import suppress
from pathlib import Path

from . import parse

logger = logging.getLogger(__name__)
PERSON_WAIT = 15 * 60.0  # seconds a puzzle waits for a person
SETTLE = (45.0, 90.0)  # seconds on the page before the session's first search


class NeedsPerson(Exception):
    """The "I'm not a robot" box asked for more than a tick, and nobody solved it."""


class Searcher:
    def __init__(self, profile: str | Path, *, headless: bool = False, sleep=time.sleep,
                 rng: random.Random | None = None, person_wait: float = PERSON_WAIT,
                 clock=time.monotonic):  # fmt: skip
        self.profile, self.headless, self.sleep = Path(profile), headless, sleep
        self.rng = rng or random.Random()
        self.person_wait, self.clock = person_wait, clock
        self._pw = self._ctx = self.page = None
        self._mouse = (640.0, 450.0)
        self._settled = False

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

    # A person's hands -------------------------------------------------------------------

    def _pause(self, low: float, high: float) -> None:
        self.sleep(self.rng.uniform(low, high))

    def _move(self, x: float, y: float) -> None:
        """The mouse to (x, y) along a curve, quick in the middle and slow at the ends."""
        (sx, sy), page = self._mouse, self.page
        cx = (sx + x) / 2 + self.rng.uniform(-120, 120)
        cy = (sy + y) / 2 + self.rng.uniform(-80, 80)
        steps = self.rng.randint(18, 40)
        for i in range(1, steps + 1):
            t = i / steps
            t = t * t * (3 - 2 * t)
            page.mouse.move((1 - t) ** 2 * sx + 2 * (1 - t) * t * cx + t * t * x,
                            (1 - t) ** 2 * sy + 2 * (1 - t) * t * cy + t * t * y)  # fmt: skip
            self.sleep(self.rng.uniform(0.006, 0.022))
        self._mouse = (x, y)

    def _point_in(self, locator) -> tuple[float, float]:
        box = locator.bounding_box(timeout=30000)
        return (box["x"] + box["width"] * self.rng.uniform(0.3, 0.7),
                box["y"] + box["height"] * self.rng.uniform(0.35, 0.65))  # fmt: skip

    def _click(self, locator) -> None:
        """Travel to it, rest a moment, press and release."""
        self._move(*self._point_in(locator))
        self._pause(0.25, 0.9)
        self.page.mouse.down()
        self._pause(0.05, 0.16)
        self.page.mouse.up()

    def _type(self, text: str) -> None:
        for ch in text:
            self.page.keyboard.type(ch)
            self._pause(0.09, 0.3)
            if self.rng.random() < 0.08:
                self._pause(0.4, 1.1)  # a hesitation

    def _look_over(self, low: float = 3.0, high: float = 8.0) -> None:
        """Look the page over: the mouse drifts, the page scrolls down a little and back."""
        for _ in range(self.rng.randint(1, 3)):
            self._move(self.rng.uniform(200, 1000), self.rng.uniform(150, 700))
            self._pause(0.4, 1.5)
        with suppress(Exception):
            down = self.rng.randint(150, 500)
            self.page.mouse.wheel(0, down)
            self._pause(1.0, 3.0)
            self.page.mouse.wheel(0, -down)
        self._pause(low, high)

    # The search -------------------------------------------------------------------------

    def _ticked(self, box) -> bool:
        with suppress(Exception):
            return box.get_attribute("aria-checked", timeout=5000) == "true"
        return False

    def _puzzle_showing(self) -> bool:
        puzzle = self.page.locator("iframe[title*='challenge']")
        with suppress(Exception):
            return puzzle.count() > 0 and puzzle.first.is_visible()
        return False

    def search(self, last_name: str) -> bytes:
        """The first page of results for a last name (a prefix: "Z" finds every Z name)."""
        page = self.page
        page.goto(parse.URL, wait_until="domcontentloaded", timeout=90000)
        if not self._settled:
            self._look_over(*SETTLE)
            self._settled = True
        else:
            self._look_over()
        self._click(page.locator("#txtLName"))
        self._pause(0.3, 0.8)
        self._type(last_name)
        self._pause(0.8, 2.5)
        box = page.frame_locator("iframe[title='reCAPTCHA']").locator("#recaptcha-anchor")
        self._click(box)
        for _ in range(30):
            self.sleep(0.5)
            if self._ticked(box):
                break
            if self._puzzle_showing():
                self._wait_for_person(box, last_name)
                break
        else:
            raise NeedsPerson(f"search {last_name!r}: the box wasn't ticked")
        self._pause(0.8, 2.5)
        with page.expect_navigation(wait_until="domcontentloaded", timeout=90000):
            self._click(page.locator("input[name=btnMission]"))
        self._look_over(4.0, 10.0)
        return page.content().encode()

    def _wait_for_person(self, box, last_name: str) -> None:
        """A picture puzzle: wait for someone at the window to solve it."""
        logger.warning("DOC search %r: the 'I'm not a robot' box wants a picture puzzle; "
                       "waiting up to %d minutes for someone to solve it in the Chrome window",
                       last_name, self.person_wait // 60)  # fmt: skip
        deadline = self.clock() + self.person_wait
        while self.clock() < deadline:
            self.sleep(2.0)
            if self._ticked(box):
                logger.info("DOC search %r: solved; going on", last_name)
                return
        raise NeedsPerson(
            f"search {last_name!r}: the box asked for a picture puzzle, and nobody "
            f"solved it in {self.person_wait // 60:.0f} minutes"
        )
