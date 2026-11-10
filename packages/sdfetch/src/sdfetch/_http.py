"""HTTP: retries with backoff, a pause between requests to the same host, block-page checks."""

from __future__ import annotations

import atexit
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

from ._ladder import BROWSER_RUNGS, LADDER, HostMemory
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
    def __init__(self, response):
        super().__init__(f"{response.url}: HTTP {response.status_code}")
        self.response = response


def http_error(resp) -> requests.HTTPError:
    """The same error for a bad status whichever rung fetched it."""
    return requests.HTTPError(f"HTTP {resp.status_code} for {resp.url}", response=resp)


def blocked(resp, phrases: Iterable[str] = ()) -> str | None:
    """Why this response is a block page, or None. Only text responses are checked."""
    kind = resp.headers.get("Content-Type") or resp.headers.get("content-type") or ""
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


def retry_after(resp) -> float | None:
    """Seconds from a Retry-After header (a number or an HTTP date)."""
    headers = resp.headers if resp is not None else {}
    value = headers.get("Retry-After") or headers.get("retry-after")
    if not value:
        return None
    if value.strip().isdigit():
        return float(value)
    try:
        return max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
    except (TypeError, ValueError):
        return None


class Fetcher:
    """Fetches into a Store, one request at a time per host, climbing the ladder when blocked.

    - Strategy: "auto" (the default) starts on the rung that last worked for the host
      (HostMemory) and climbs one rung each time a response is a block page, up to
      max_strategy. POSTs stop at "impersonate"; the browsers only navigate. A rung name
      ("http", "impersonate", "stealth", "patchright", "camoufox", "nodriver") pins it.
    - Pause: at least `delay` seconds between requests to a host (`delays` per host), shared
      by every Fetcher in the process.
    - Retries: connection errors and HTTP 429/5xx, up to `retries` attempts on a rung, waiting
      Retry-After when the server sends it and otherwise `backoff` × 2ⁿ seconds. With a pinned
      strategy, a block page is retried the same way with a wait four times longer.
    - `fetch(url, name)` returns the stored copy when there is one; refresh=True fetches again.
      The manifest records the rung that fetched each file ("via").

    Browsers start when first needed and stay open until close() (or the end of a `with`).
    """

    def __init__(
        self,
        store: Store | str | Path | None = None,
        *,
        strategy: str = "auto",
        max_strategy: str = LADDER[-1],
        headless: bool = True,
        memory: HostMemory | str | Path | bool | None = None,
        delay: float = 1.0,
        delays: dict[str, float] | None = None,
        user_agent: str | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 120,
        retries: int = 4,
        backoff: float = 5,
        block_phrases: Iterable[str] = (),
        session: requests.Session | None = None,
        clients: dict[str, Callable] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if strategy != "auto" and strategy not in LADDER:
            raise ValueError(f"strategy must be 'auto' or one of {LADDER}")
        if max_strategy not in LADDER:
            raise ValueError(f"max_strategy must be one of {LADDER}")
        self.store = Store(store) if isinstance(store, (str, Path)) else store
        self.strategy, self.max_strategy, self.headless = strategy, max_strategy, headless
        if memory is False:
            self.memory = None
        elif isinstance(memory, HostMemory):
            self.memory = memory
        else:
            self.memory = HostMemory(memory)
        self.delay, self.delays = delay, delays or {}
        self.timeout, self.retries, self.backoff = timeout, retries, backoff
        self.block_phrases = list(block_phrases)
        self.sleep = sleep
        self.session = session or requests.Session()
        self.session.headers.update(BROWSER_HEADERS)
        if user_agent:
            self.session.headers["User-Agent"] = user_agent
        self.session.headers.update(headers or {})
        self.clients = dict(clients or {})  # rung → send(method, url, **kwargs), for tests
        self._impersonate = None
        self._browsers: dict = {}
        self.requests = 0  # sent, including retries
        self.last_via: str | None = None  # the rung that answered the last request

    def __enter__(self) -> Fetcher:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        """Close any browsers this Fetcher started."""
        for b in self._browsers.values():
            b.close()
        self._browsers.clear()

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

    # Rungs ---------------------------------------------------------------

    def _rungs(self, method: str, url: str) -> list[str]:
        if self.strategy != "auto":
            if method != "GET" and self.strategy in BROWSER_RUNGS:
                raise ValueError(f"{self.strategy} only navigates; use http or impersonate")
            return [self.strategy]
        top = LADDER.index(self.max_strategy)
        if method != "GET":
            top = min(top, LADDER.index("impersonate"))
        host = urlsplit(url).hostname or ""
        start = self.memory.start(host) if self.memory else "http"
        return list(LADDER[min(LADDER.index(start), top) : top + 1])

    def _send(self, rung: str, method: str, url: str, **kwargs):
        if rung in self.clients:
            return self.clients[rung](method, url, **kwargs)
        if rung == "http":
            return self.session.request(method, url, **kwargs)
        if rung == "impersonate":
            return self._send_impersonate(method, url, **kwargs)
        if kwargs.get("params"):
            url = requests.Request("GET", url, params=kwargs["params"]).prepare().url
        return self._browser(rung).get(url)

    def _send_impersonate(self, method: str, url: str, **kwargs):
        from curl_cffi import requests as curl
        from curl_cffi.requests.exceptions import RequestException, Timeout

        if self._impersonate is None:
            # The newest Chrome-on-macOS fingerprint curl_cffi has. Its own headers match
            # that fingerprint's version, so they're used as they are.
            targets = [b.value for b in curl.BrowserType if b.value.endswith("_macos")]
            chrome = sorted(
                (t for t in targets if t.startswith("chrome")),
                key=lambda t: int(t.removeprefix("chrome").removesuffix("_macos")),
            )
            self._impersonate = curl.Session(impersonate=chrome[-1] if chrome else "chrome")
        try:
            return self._impersonate.request(method, url, **kwargs)
        except Timeout as exc:
            raise requests.Timeout(str(exc)) from exc
        except RequestException as exc:
            raise requests.ConnectionError(str(exc)) from exc

    def _browser(self, rung: str):
        if rung not in self._browsers:
            from ._browser import Browser

            if not self._browsers:
                atexit.register(self.close)
            self._browsers[rung] = Browser(rung, headless=self.headless, timeout=self.timeout)
        return self._browsers[rung]

    # Requests ------------------------------------------------------------

    def _wait(self, state: RetryCallState) -> float:
        exc = state.outcome.exception() if state.outcome else None
        base = self.backoff * 2 ** (state.attempt_number - 1)
        if isinstance(exc, RetryableStatus):
            return retry_after(exc.response) or base
        if isinstance(exc, Blocked):
            return base * 4
        return base

    def _once(self, rung: str, method: str, url: str, check_blocked: bool, **kwargs):
        self._pause(url)
        self.requests += 1
        if rung in ("http", "impersonate"):
            kwargs.setdefault("timeout", self.timeout)
        resp = self._send(rung, method, url, **kwargs)
        if check_blocked and (reason := blocked(resp, self.block_phrases)):
            raise Blocked(url, f"{reason} ({rung})", resp.status_code)
        if resp.status_code in RETRY_STATUSES:
            raise RetryableStatus(resp)
        if resp.status_code >= 400:
            raise http_error(resp)
        return resp

    def _on_rung(self, rung: str, method: str, url: str, check_blocked: bool, retry_blocked: bool,
                 **kwargs):  # fmt: skip
        retryable = (requests.ConnectionError, requests.Timeout, RetryableStatus)
        if retry_blocked:
            retryable += (Blocked,)
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
                    return self._once(rung, method, url, check_blocked, **kwargs)
        except RetryableStatus as exc:
            raise http_error(exc.response) from exc
        raise AssertionError("unreachable")

    def request(self, method: str, url: str, *, check_blocked: bool = True, **kwargs):
        """One request, with the pause, retries and escalation, not stored. Returns a
        requests-like response (requests, curl_cffi, or a browser's). Raises Blocked when the
        last rung tried is blocked too, or requests.HTTPError for a status that isn't retried
        or still fails."""
        rungs = self._rungs(method, url)
        host = urlsplit(url).hostname or ""
        last: Blocked | None = None
        for i, rung in enumerate(rungs):
            try:
                resp = self._on_rung(
                    rung, method, url, check_blocked, retry_blocked=len(rungs) == 1, **kwargs
                )
            except Blocked as exc:
                last = exc
                if i + 1 < len(rungs):
                    logger.info("%s: blocked on %s; trying %s", url, rung, rungs[i + 1])
                continue
            self.last_via = rung
            if self.strategy == "auto" and self.memory:
                self.memory.record(host, rung)
            return resp
        assert last is not None
        raise last

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
        if data is not None:
            kwargs["data"] = data
        resp = self.request(method, url, **kwargs)
        self.store.save(
            name,
            resp.content,
            url,
            method=method,
            status=resp.status_code,
            note={"via": self.last_via, **(note or {})},
            allow_empty=allow_empty,
        )
        return resp.content

    def text(self, url: str, name: str, **kwargs) -> str:
        """fetch(), decoded as UTF-8 (undecodable bytes replaced)."""
        return self.fetch(url, name, **kwargs).decode("utf-8", "replace")
