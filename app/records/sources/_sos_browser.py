"""The Secretary of State's document viewers, read through a Chrome window.

`BrowserFetcher.request("GET", url)` opens the URL in the window and answers with what the
browser got: the body of the page (or page image) it navigated to, the URL it ended up at
(an error page after a redirect), the status and headers. So every request is the browser's
own, with its cookies, Referer and connection, and `_sos_search.assemble()` works on it as on
plain HTTP.

The window is Google Chrome (patchright) with a profile kept between runs, headless or not
(`SOS_BROWSER=headless` or `headed`).
"""

from __future__ import annotations

from contextlib import suppress
from pathlib import Path


class Page:
    """What the browser got for one navigation, shaped like an HTTP response."""

    def __init__(self, content: bytes, url: str, status: int, headers: dict):
        self.content, self.url, self.status_code = content, url, status
        self.headers = headers

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", "replace")


class BrowserFetcher:
    def __init__(self, profile: str | Path, *, headless: bool = True):
        self.profile, self.headless = Path(profile), headless
        self._pw = self._ctx = self.page = None

    def start(self) -> BrowserFetcher:
        from patchright.sync_api import sync_playwright

        self.profile.mkdir(parents=True, exist_ok=True)
        self._pw = sync_playwright().start()
        self._ctx = self._pw.chromium.launch_persistent_context(
            str(self.profile), channel="chrome", headless=self.headless, no_viewport=True,
            locale="en-US", args=["--window-size=1280,900"],
        )  # fmt: skip
        self.page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
        return self

    def close(self) -> None:
        for close in (getattr(self._ctx, "close", None), getattr(self._pw, "stop", None)):
            if close:
                with suppress(Exception):
                    close()
        self._pw = self._ctx = self.page = None

    def request(self, method: str, url: str, *, timeout: float = 120, headers=None, **kwargs):
        if method != "GET":
            raise NotImplementedError("the window only opens pages (GET)")
        if self.page is None:
            self.start()
        referer = (headers or {}).get("Referer")
        response = self.page.goto(url, wait_until="load", timeout=timeout * 1000,
                                  referer=referer)  # fmt: skip
        if response is None:
            return Page(b"", self.page.url, 0, {})
        return Page(response.body(), self.page.url, response.status,
                    {k.title(): v for k, v in response.headers.items()})  # fmt: skip
