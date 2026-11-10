"""HTTP: retries with backoff, a pause between requests to the same host, block-page checks."""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable, Iterable
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit

import requests
from tenacity import RetryCallState, Retrying, retry_if_exception, stop_after_attempt

from ._store import Store

logger = logging.getLogger(__name__)

# Every request looks like desktop Chrome on a Mac: nothing names the project or a bot, and
# nothing goes out as python-requests or Python-urllib. Keep CHROME close to the current
# stable release (a stale version stands out); Chrome reports only the major version.
CHROME = "157"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
    f"Chrome/{CHROME}.0.0.0 Safari/537.36"
)
BROWSER_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,"
        "image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "sec-ch-ua": f'"Google Chrome";v="{CHROME}", "Chromium";v="{CHROME}", "Not_A Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
    "Upgrade-Insecure-Requests": "1",
}
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

# Challenge and block pages served in place of the content. Each pattern names its source.
BLOCK_PATTERNS = [
    ("Cloudflare challenge", re.compile(r"cf-chl|challenge-platform|<title>Just a moment", re.I)),
    ("Cloudflare block", re.compile(r"Attention Required! \| Cloudflare", re.I)),
    ("Akamai block", re.compile(r"<title>Access Denied</title>.*Reference #", re.I | re.S)),
    ("Imperva block", re.compile(r"_Incapsula_Resource", re.I)),
    ("PerimeterX challenge", re.compile(r"px-captcha", re.I)),
]

_hosts_lock = threading.Lock()
_host_next: dict[str, float] = {}  # host → earliest time for its next request (all Fetchers)


class Blocked(Exception):
    """The site answered with a challenge or block page instead of the content."""

    def __init__(self, url: str, reason: str, status: int | None = None):
        super().__init__(f"{url}: {reason} (HTTP {status})")
        self.url, self.reason, self.status = url, reason, status


class RetryableStatus(Exception):
    def __init__(self, response: requests.Response):
        super().__init__(f"{response.url}: HTTP {response.status_code}")
        self.response = response


def blocked(resp: requests.Response, phrases: Iterable[str] = ()) -> str | None:
    """Why this response is a block page, or None. Only text responses are checked."""
    kind = resp.headers.get("Content-Type", "")
    if kind and not kind.startswith(("text/", "application/xhtml")):
        return None
    head = resp.content[:65536].decode("utf-8", "replace")
    for reason, pattern in BLOCK_PATTERNS:
        if pattern.search(head):
            return reason
    lowered = head.lower()
    for phrase in phrases:
        if phrase.lower() in lowered:
            return f"block phrase {phrase!r}"
    return None


def retry_after(resp: requests.Response | None) -> float | None:
    """Seconds from a Retry-After header (a number or an HTTP date)."""
    value = resp.headers.get("Retry-After") if resp is not None else None
    if not value:
        return None
    if value.strip().isdigit():
        return float(value)
    try:
        return max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
    except (TypeError, ValueError):
        return None


class Fetcher:
    """Fetches into a Store, one request at a time per host.

    - Pause: at least `delay` seconds between requests to a host (`delays` per host), shared
      by every Fetcher in the process.
    - Retries: connection errors and HTTP 429/5xx, up to `retries` attempts, waiting
      Retry-After when the server sends it and otherwise `backoff` × 2ⁿ seconds. A block page
      is retried the same way with a wait four times longer, then raised as Blocked.
    - `fetch(url, name)` returns the stored copy when there is one; refresh=True fetches again.
    """

    def __init__(
        self,
        store: Store | str | Path | None = None,
        *,
        delay: float = 1.0,
        delays: dict[str, float] | None = None,
        user_agent: str | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 120,
        retries: int = 4,
        backoff: float = 5,
        block_phrases: Iterable[str] = (),
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.store = Store(store) if isinstance(store, (str, Path)) else store
        self.delay, self.delays = delay, delays or {}
        self.timeout, self.retries, self.backoff = timeout, retries, backoff
        self.block_phrases = list(block_phrases)
        self.sleep = sleep
        self.session = session or requests.Session()
        self.session.headers.update(BROWSER_HEADERS)
        if user_agent:
            self.session.headers["User-Agent"] = user_agent
        self.session.headers.update(headers or {})
        self.requests = 0  # sent, including retries

    # Pause ---------------------------------------------------------------

    def _pause(self, url: str) -> None:
        host = urlsplit(url).hostname or ""
        gap = self.delays.get(host, self.delay)
        with _hosts_lock:
            t = time.monotonic()
            start = max(t, _host_next.get(host, 0.0))
            _host_next[host] = start + gap
        if start > t:
            self.sleep(start - t)

    # Requests ------------------------------------------------------------

    def _wait(self, state: RetryCallState) -> float:
        exc = state.outcome.exception() if state.outcome else None
        base = self.backoff * 2 ** (state.attempt_number - 1)
        if isinstance(exc, RetryableStatus):
            return retry_after(exc.response) or base
        if isinstance(exc, Blocked):
            return base * 4
        return base

    def _once(self, method: str, url: str, check_blocked: bool, **kwargs) -> requests.Response:
        self._pause(url)
        self.requests += 1
        kwargs.setdefault("timeout", self.timeout)
        resp = self.session.request(method, url, **kwargs)
        if check_blocked and (reason := blocked(resp, self.block_phrases)):
            raise Blocked(url, reason, resp.status_code)
        if resp.status_code in RETRY_STATUSES:
            raise RetryableStatus(resp)
        resp.raise_for_status()
        return resp

    def request(
        self, method: str, url: str, *, check_blocked: bool = True, **kwargs
    ) -> requests.Response:
        """One request, with the pause and retries, not stored. Raises Blocked, or
        requests.HTTPError for a status that isn't retried or still fails."""
        retryable = (requests.ConnectionError, requests.Timeout, RetryableStatus, Blocked)
        try:
            for attempt in Retrying(
                stop=stop_after_attempt(self.retries),
                wait=self._wait,
                retry=retry_if_exception(lambda e: isinstance(e, retryable)),
                sleep=self.sleep,
                before_sleep=lambda s: logger.info(
                    "retrying %s after %s", url, s.outcome.exception() if s.outcome else ""
                ),
                reraise=True,
            ):
                with attempt:
                    return self._once(method, url, check_blocked, **kwargs)
        except RetryableStatus as exc:
            exc.response.raise_for_status()  # as an HTTPError, like any other bad status
            raise
        raise AssertionError("unreachable")

    def fetch(
        self,
        url: str,
        name: str,
        *,
        data: dict | bytes | None = None,
        refresh: bool = False,
        note: dict | None = None,
        allow_empty: bool = False,
        **kwargs,
    ) -> bytes:
        """The body of url, from the store when it's there; otherwise fetched (a POST when
        data is given) and stored at name. A refresh that comes back empty keeps the stored
        copy and raises EmptyResponse."""
        if self.store is None:
            raise ValueError("fetch() needs a store; use request() for unstored requests")
        if not refresh and self.store.has(name):
            return self.store.read(name)
        method = "GET" if data is None else "POST"
        resp = self.request(method, url, data=data, **kwargs)
        self.store.save(
            name,
            resp.content,
            url,
            method=method,
            status=resp.status_code,
            note=note,
            allow_empty=allow_empty,
        )
        return resp.content

    def text(self, url: str, name: str, **kwargs) -> str:
        """fetch(), decoded as UTF-8 (undecodable bytes replaced)."""
        return self.fetch(url, name, **kwargs).decode("utf-8", "replace")
