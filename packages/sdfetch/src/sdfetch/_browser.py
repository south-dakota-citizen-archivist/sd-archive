"""Real browsers behind one interface: the upper rungs of the escalation ladder.

    with sdfetch.browser("patchright") as b:
        resp = b.get("https://example.org/")   # waits out a challenge page if one appears
        b.page.click("text=Next")               # the Playwright page, for anything more

Strategies, lightest first:
- "stealth": the installed Google Chrome, driven by Playwright with playwright-stealth
- "patchright": the installed Google Chrome, driven by patchright (no automation flags)
- "camoufox": Camoufox, a Firefox build with a consistent spoofed fingerprint
  (its browser is downloaded once by `python -m camoufox fetch`)
- "nodriver": the installed Google Chrome over CDP, no WebDriver or Playwright at all

The Chrome strategies use the real Chrome on this machine (channel="chrome"), so the browser
version, TLS fingerprint and headers are all a real, current Chrome. Headless Chrome calls
itself "HeadlessChrome"; that's replaced with "Chrome" everywhere it shows.
"""

from __future__ import annotations

import time
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path

from ._http import BLOCK_PATTERNS

STRATEGIES = ("stealth", "patchright", "camoufox", "nodriver")


@dataclass
class BrowserResponse:
    """What a browser got: like a requests.Response for the parts sdfetch uses."""

    url: str
    status_code: int
    content: bytes
    headers: dict = field(default_factory=dict)

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", "replace")

    @property
    def ok(self) -> bool:
        return self.status_code < 400


def _challenged(html: str) -> bool:
    return any(p.search(html[:65536]) for _, p in BLOCK_PATTERNS)


def _content(page, tries: int = 30) -> str:
    """The page's HTML, waiting out a navigation in progress (a challenge reloading)."""
    for _ in range(tries):
        try:
            return page.content()
        except Exception as exc:
            if "navigating" not in str(exc):
                raise
            with suppress(Exception):
                page.wait_for_load_state("domcontentloaded", timeout=5000)
    return page.content()


def _native_ua(ua: str) -> str:
    return ua.replace("HeadlessChrome", "Chrome")


class Browser:
    """One running browser. get() navigates and returns the page (or file) it ends on."""

    def __init__(
        self,
        strategy: str = "stealth",
        *,
        headless: bool = True,
        timeout: float = 60,
        har: str | Path | None = None,
    ):
        """har: record every request the browser makes to this HAR file, written on close()
        (not nodriver, which has no recorder)."""
        if strategy not in STRATEGIES:
            raise ValueError(f"strategy must be one of {STRATEGIES}")
        if har and strategy == "nodriver":
            raise ValueError("nodriver can't record a HAR; use stealth, patchright or camoufox")
        self.strategy, self.headless, self.timeout = strategy, headless, timeout
        self.har = Path(har) if har else None
        if self.har:
            self.har.parent.mkdir(parents=True, exist_ok=True)
        self.page = None  # the Playwright page (None for nodriver)
        self._stack: list = []  # things to close, last first
        getattr(self, f"_start_{strategy}")()

    # Starting -------------------------------------------------------------

    def _chrome_context(self, p, *, stealth: bool) -> None:
        browser = p.chromium.launch(channel="chrome", headless=self.headless)
        self._stack.append(browser.close)
        probe = browser.new_page()
        ua = _native_ua(probe.evaluate("navigator.userAgent"))
        probe.close()
        kwargs = {"user_agent": ua, "locale": "en-US", **self._har_kwargs()}
        if stealth:
            from playwright_stealth import Stealth

            ctx = browser.new_context(**kwargs)
            Stealth(navigator_user_agent_override=ua).apply_stealth_sync(ctx)
        else:
            ctx = browser.new_context(**kwargs, no_viewport=not self.headless)
        self._stack.append(ctx.close)
        self.context, self.page = ctx, ctx.new_page()

    def _start_stealth(self) -> None:
        from playwright.sync_api import sync_playwright

        p = sync_playwright().start()
        self._stack.append(p.stop)
        self._chrome_context(p, stealth=True)

    def _start_patchright(self) -> None:
        from patchright.sync_api import sync_playwright

        p = sync_playwright().start()
        self._stack.append(p.stop)
        self._chrome_context(p, stealth=False)

    def _start_camoufox(self) -> None:
        from camoufox.sync_api import Camoufox

        # Claim this machine's OS, so canvas and WebGL can show the real ones; otherwise
        # Camoufox randomizes them, which reads as a privacy-hardened Firefox, not a stock one
        cm = Camoufox(headless=self.headless, locale="en-US", humanize=True, os="macos")
        browser = cm.__enter__()
        self._stack.append(lambda: cm.__exit__(None, None, None))
        self.context = browser.new_context(**self._har_kwargs())
        self._stack.append(self.context.close)  # closing it writes the HAR
        self.page = self.context.new_page()

    def _har_kwargs(self) -> dict:
        return {"record_har_path": str(self.har), "record_har_content": "embed"} if self.har else {}

    def _start_nodriver(self) -> None:
        import logging

        import nodriver

        logging.getLogger("nodriver").setLevel(logging.WARNING)  # its INFO is process chatter
        self._loop = nodriver.loop()
        self._nd = self._loop.run_until_complete(nodriver.start(headless=self.headless))

        def stop():
            import atexit

            self._nd.stop()
            atexit.unregister(self._nd.stop)  # nodriver's own exit hook; it's stopped now

        self._stack.append(stop)
        ua = self._loop.run_until_complete(self._nd.main_tab.evaluate("navigator.userAgent"))
        if "HeadlessChrome" in ua:
            from nodriver import cdp

            self._loop.run_until_complete(
                self._nd.main_tab.send(cdp.network.set_user_agent_override(_native_ua(ua)))
            )

    # Fetching -------------------------------------------------------------

    def get(self, url: str, *, challenge_wait: float = 30) -> BrowserResponse:
        """Navigate to url. If a challenge page appears, wait up to challenge_wait seconds for
        it to clear; the response is whatever is there then (sdfetch checks it for blocks)."""
        if self.strategy == "nodriver":
            return self._get_nodriver(url, challenge_wait)
        page = self.page
        documents = []

        def on_response(resp):
            if resp.request.resource_type == "document" and resp.frame == page.main_frame:
                documents.append(resp)

        page.on("response", on_response)
        try:
            try:
                first = page.goto(url, wait_until="domcontentloaded", timeout=self.timeout * 1000)
            except Exception as exc:  # a file the browser downloads instead of showing
                if "Download is starting" not in str(exc):
                    raise
                r = self.context.request.get(url, timeout=self.timeout * 1000)
                return BrowserResponse(r.url, r.status, r.body(), dict(r.headers))
            kind = (first.headers.get("content-type", "") if first else "").lower()
            if first and kind and "html" not in kind:  # JSON, XML, a PDF shown inline
                return BrowserResponse(first.url, first.status, first.body(), dict(first.headers))
            deadline = time.monotonic() + challenge_wait
            while _challenged(_content(page)) and time.monotonic() < deadline:
                page.wait_for_timeout(1000)
            with suppress(Exception):  # a slow subresource; the document is there
                page.wait_for_load_state("load", timeout=10_000)
            last = documents[-1] if documents else first
            return BrowserResponse(
                page.url,
                last.status if last else 200,
                _content(page).encode("utf-8"),
                dict(last.headers) if last else {},
            )
        finally:
            page.remove_listener("response", on_response)

    def _get_nodriver(self, url: str, challenge_wait: float) -> BrowserResponse:
        async def go():
            tab = await self._nd.get(url)
            await tab.wait(1)
            deadline = time.monotonic() + challenge_wait
            html = await tab.get_content()
            while _challenged(html) and time.monotonic() < deadline:
                await tab.wait(1)
                html = await tab.get_content()
            return tab.url if hasattr(tab, "url") else url, html

        final, html = self._loop.run_until_complete(go())
        # nodriver doesn't report the status; a page that's still a challenge is caught by
        # sdfetch's block check
        return BrowserResponse(str(final or url), 200, html.encode("utf-8"), {})

    def close(self) -> None:
        while self._stack:
            with suppress(Exception):  # closing what's left matters more
                self._stack.pop()()


@contextmanager
def browser(
    strategy: str = "stealth",
    *,
    headless: bool = True,
    timeout: float = 60,
    har: str | Path | None = None,
):
    """A running browser for one block of work, closed afterwards (writing the HAR, if any)."""
    b = Browser(strategy, headless=headless, timeout=timeout, har=har)
    try:
        yield b
    finally:
        b.close()
