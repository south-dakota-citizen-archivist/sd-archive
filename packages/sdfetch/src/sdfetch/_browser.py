"""Browsers behind one interface.

    with sdfetch.browser("stealth") as b:
        resp = b.get("https://example.org/")   # waits for an interstitial page to clear
        b.page.click("text=Next")               # the Playwright page, for anything more

Backends (STRATEGIES): "stealth" (Playwright with playwright-stealth), "patchright",
"camoufox" (its browser is downloaded once by `python -m camoufox fetch`) and "nodriver".
The Chrome-based ones use the installed Google Chrome when there is one, and otherwise
Playwright's Chromium.
"""

from __future__ import annotations

import time
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlsplit

from ._http import BLOCK_PATTERNS
from ._redact import redact

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

    @property
    def reason(self) -> str:
        return "OK" if self.ok else ""

    def json(self, **kwargs):
        import json

        return json.loads(self.content, **kwargs)

    def raise_for_status(self) -> None:
        if not self.ok:
            import requests

            raise requests.HTTPError(
                f"HTTP {self.status_code} for {redact(self.url)}", response=self
            )


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


def _playwright_proxy(url: str | None) -> dict | None:
    """A proxy URL as Playwright's proxy setting: server, and username and password if any."""
    if not url:
        return None
    parts = urlsplit(url)
    port = f":{parts.port}" if parts.port else ""
    out = {"server": f"{parts.scheme}://{parts.hostname}{port}"}
    if parts.username:
        out["username"] = unquote(parts.username)
    if parts.password:
        out["password"] = unquote(parts.password)
    return out


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
        proxy: str | None = None,
    ):
        """har: record every request the browser makes to this HAR file, written on close()
        (not nodriver, which has no recorder). proxy: a proxy URL (http, https or socks5)
        for all of the browser's traffic."""
        if strategy not in STRATEGIES:
            raise ValueError(f"strategy must be one of {STRATEGIES}")
        if har and strategy == "nodriver":
            raise ValueError("nodriver can't record a HAR; use stealth, patchright or camoufox")
        self.proxy = _playwright_proxy(proxy)
        if self.proxy and strategy == "nodriver" and "username" in self.proxy:
            raise ValueError("nodriver can't use a proxy that needs a username and password")
        self.strategy, self.headless, self.timeout = strategy, headless, timeout
        self.har = Path(har) if har else None
        if self.har:
            self.har.parent.mkdir(parents=True, exist_ok=True)
        self.page = None  # the Playwright page (None for nodriver)
        self._stack: list = []  # things to close, last first
        getattr(self, f"_start_{strategy}")()

    # Starting -------------------------------------------------------------

    def _chrome_context(self, p, *, stealth: bool) -> None:
        try:
            browser = p.chromium.launch(channel="chrome", headless=self.headless, proxy=self.proxy)
        except Exception:  # no Google Chrome here (a CI runner): Playwright's own Chromium
            browser = p.chromium.launch(headless=self.headless, proxy=self.proxy)
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

        # The OS matches this machine's, so canvas and WebGL aren't randomized
        cm = Camoufox(
            headless=self.headless,
            locale="en-US",
            humanize=True,
            os="macos",
            proxy=self.proxy,
            geoip=bool(self.proxy),  # timezone and locale to match the route's exit address
        )
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
        args = [f"--proxy-server={self.proxy['server']}"] if self.proxy else None
        self._nd = self._loop.run_until_complete(
            nodriver.start(headless=self.headless, browser_args=args)
        )

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

    def cookies(self) -> list[dict]:
        """The browser's cookies, as dicts (name, value, domain, path, expires, …)."""
        if self.strategy == "nodriver":
            found = self._loop.run_until_complete(self._nd.cookies.get_all())
            return [
                {"name": c.name, "value": c.value, "domain": c.domain, "path": c.path,
                 "expires": c.expires if c.expires and c.expires > 0 else None,
                 "secure": c.secure, "httpOnly": c.http_only}
                for c in found
            ]  # fmt: skip
        return [dict(c) for c in self.context.cookies()]

    def add_cookies(self, cookies: list[dict]) -> None:
        """Give the browser these cookies (Playwright's format; not supported by nodriver)."""
        if cookies and self.strategy != "nodriver":
            self.context.add_cookies(cookies)

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
    proxy: str | None = None,
):
    """A running browser for one block of work, closed afterwards (writing the HAR, if any)."""
    b = Browser(strategy, headless=headless, timeout=timeout, har=har, proxy=proxy)
    try:
        yield b
    finally:
        b.close()
