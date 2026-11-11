"""HTTP: retries with backoff, a pause between requests to the same host, block-page checks."""

from __future__ import annotations

import atexit
import logging
import random
import re
import threading
import time
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit

import certifi
import requests
from tenacity import RetryCallState, Retrying, retry_if_exception, stop_after_attempt

from ._config import Config, load_config
from ._ladder import DIRECT, HostMemory, RungUnavailable, get_strategy, ladder
from ._pacing import Budget, check_window, jittered
from ._redact import redact
from ._store import Store
from .session import CookieJar, LoginRequired, login_for, looks_logged_out

logger = logging.getLogger(__name__)

# Default request headers: desktop Chrome on macOS. Keep CHROME at the current stable
# major version.
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


class Blocked(requests.RequestException):
    """The site answered with a challenge or block page instead of the content."""

    def __init__(self, url: str, reason: str, status: int | None = None):
        super().__init__(f"{redact(url)}: {reason} (HTTP {status})")
        self.url, self.reason, self.status = url, reason, status


class RetryableStatus(Exception):
    def __init__(self, response):
        super().__init__(f"{redact(response.url)}: HTTP {response.status_code}")
        self.response = response


def http_error(resp) -> requests.HTTPError:
    """The same error for a bad status whichever rung fetched it."""
    return requests.HTTPError(f"HTTP {resp.status_code} for {redact(resp.url)}", response=resp)


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
    - warc=True (or a folder): every successful response also goes into a WARC file, and the
      manifest line says where (`warc`, `warc_offset`). har=True (or a folder): each browser
      the Fetcher starts records a HAR, written when the Fetcher closes.

    Browsers start when first needed and stay open until close() (or the end of a `with`).
    """

    def __init__(
        self,
        store: Store | str | Path | None = None,
        *,
        strategy: str = "auto",
        max_strategy: str | None = None,
        routes: list[str] | None = None,
        config: Config | dict | str | Path | bool | None = None,
        headless: bool = True,
        memory: HostMemory | str | Path | bool | None = None,
        delay: float = 1.0,
        delays: dict[str, float] | None = None,
        jitter: float = 0.0,
        hours: str | None = None,
        days: str | None = None,
        tz: str | None = None,
        budget: int | None = None,
        budget_file: str | Path | None = None,
        user_agent: str | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 120,
        retries: int = 4,
        backoff: float = 5,
        block_phrases: Iterable[str] = (),
        cookies: bool | str | Path = False,
        logged_out: Iterable[str] = (),
        raise_errors: bool = True,
        session: requests.Session | None = None,
        clients: dict[str, Callable] | None = None,
        warc: bool | str | Path = False,
        har: bool | str | Path = False,
        sleep: Callable[[float], None] = time.sleep,
        rng: random.Random | None = None,
        clock: Callable[[], datetime] | None = None,
    ):
        if strategy != "auto":
            get_strategy(strategy)  # raises for an unknown name
        if max_strategy is not None:
            get_strategy(max_strategy)
        self.store = Store(store) if isinstance(store, (str, Path)) else store
        self.strategy, self.max_strategy, self.headless = strategy, max_strategy, headless
        self.routes = list(routes) if routes else None
        self.config = load_config(config)
        if memory is False:
            self.memory = None
        elif isinstance(memory, HostMemory):
            self.memory = memory
        else:
            self.memory = HostMemory(memory)
        self.delay, self.delays = delay, delays or {}
        self.jitter, self.hours, self.days, self.tz = jitter, hours, days, tz
        self.budget, self._budget = budget, Budget(budget_file)
        self.rng = rng or random.Random()
        self.clock = clock or (lambda: datetime.now(UTC))
        self.timeout, self.retries, self.backoff = timeout, retries, backoff
        self.block_phrases = list(block_phrases)
        self.raise_errors = raise_errors
        self.sleep = sleep
        self.session = session or requests.Session()
        self.session.headers.update(BROWSER_HEADERS)
        if user_agent:
            self.session.headers["User-Agent"] = user_agent
        self.session.headers.update(headers or {})
        self.clients = dict(clients or {})  # rung → send(method, url, **kwargs), for tests
        # Cookies: one jar, given to every client and saved after each response
        self.jar = CookieJar(cookies or None)
        self.jar.to_jar(self.session.cookies)
        self.logged_out = list(logged_out)
        self._logging_in = False
        self._impersonate: dict = {}  # route name → curl_cffi session
        self._browsers: dict = {}  # (strategy, route name) → Browser
        # WARC and HAR folders: True puts them in the store ("warc/", "har/")
        self.warc_dir = self._folder(warc, "warc")
        self.har_dir = self._folder(har, "har")
        self._warc = None
        self.requests = 0  # sent, including retries
        self.last_via: str | None = None  # the strategy that answered the last request
        self.last_route: str = DIRECT  # and the route it went out on
        self.last_warc: tuple[Path, int] | None = None  # (file, offset) of its WARC record

    def _folder(self, value, name: str) -> Path | None:
        if value is True:
            if self.store is None:
                raise ValueError(f"{name}=True needs a store; give a folder instead")
            return self.store.root / name
        return Path(value) if value else None

    def __enter__(self) -> Fetcher:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        """Close any browsers this Fetcher started (writing their HARs) and its WARC file."""
        for b in self._browsers.values():
            b.close()
        self._browsers.clear()
        if self._warc:
            self._warc.close()
            self._warc = None

    # Settings ------------------------------------------------------------

    def _settings(self, url: str) -> dict:
        """The private config's settings for this URL's host (empty when there are none)."""
        return self.config.for_host(urlsplit(url).hostname or "")

    # Pause ---------------------------------------------------------------

    def _pause(self, url: str) -> None:
        host = urlsplit(url).hostname or ""
        settings = self._settings(url)
        gap = settings.get("delay", self.delays.get(host, self.delay))
        gap = jittered(gap, settings.get("jitter", self.jitter), self.rng)
        with _hosts_lock:
            t = time.monotonic()
            start = max(t, _host_next.get(host, 0.0))
            _host_next[host] = start + gap
        if start > t:
            self.sleep(start - t)

    # Attempts ------------------------------------------------------------

    def _attempts(self, method: str, url: str) -> list[tuple[str, str]]:
        """(route, strategy) pairs to try in order: every strategy on a route, then the next
        route. Auto mode starts where HostMemory says this host last succeeded."""
        settings = self._settings(url)
        strategy = settings.get("strategy", self.strategy)
        routes = list(settings.get("routes") or self.routes or [DIRECT])
        if strategy != "auto":
            if method != "GET" and get_strategy(strategy).browser:
                raise ValueError(f"{strategy} only navigates; use a strategy that can {method}")
            return [(route, strategy) for route in routes]
        order = ladder()
        top = settings.get("max_strategy") or self.max_strategy or order[-1]
        rungs = [
            r for r in order[: order.index(top) + 1]
            if method == "GET" or not get_strategy(r).browser
        ]  # fmt: skip
        host = urlsplit(url).hostname or ""
        route0, rung0 = (
            self.memory.start(host, rungs, routes) if self.memory else (routes[0], rungs[0])
        )
        out = []
        for i, route in enumerate(routes[routes.index(route0) :]):
            out += [(route, r) for r in (rungs[rungs.index(rung0) :] if i == 0 else rungs)]
        return out

    # Sending -------------------------------------------------------------

    def _send(self, route: str, rung: str, method: str, url: str, **kwargs):
        name, proxy = self.config.resolve_route(route)
        if rung in self.clients:
            return self.clients[rung](method, url, route=name, **kwargs)
        custom = get_strategy(rung).send
        if custom is not None:
            kwargs.setdefault("timeout", self.timeout)
            return custom(method, url, route=proxy, **kwargs)
        if rung == "http":
            if proxy:
                kwargs["proxies"] = {"http": proxy, "https": proxy}
            try:
                return self.session.request(method, url, **kwargs)
            except requests.exceptions.InvalidSchema as exc:  # e.g. socks5 without PySocks
                raise RungUnavailable(f"route {name}: {exc}") from exc
        if rung == "impersonate":
            return self._send_impersonate(name, proxy, method, url, **kwargs)
        if kwargs.get("params"):
            url = requests.Request("GET", url, params=kwargs["params"]).prepare().url
        return self._browser(rung, name, proxy).get(url)

    def _send_impersonate(self, name: str, proxy: str | None, method: str, url: str, **kwargs):
        try:
            from curl_cffi import requests as curl
            from curl_cffi.requests.exceptions import RequestException, Timeout
        except ImportError as exc:
            raise RungUnavailable(
                "impersonate: curl_cffi isn't installed (sdfetch[impersonate])"
            ) from exc

        if name not in self._impersonate:
            # The newest Chrome-on-macOS profile curl_cffi has, with its own matching headers
            targets = [b.value for b in curl.BrowserType if b.value.endswith("_macos")]
            chrome = sorted(
                (t for t in targets if t.startswith("chrome")),
                key=lambda t: int(t.removeprefix("chrome").removesuffix("_macos")),
            )
            # certifi's CA list, the one requests uses: curl_cffi's own lacks some chains
            # (Sectigo's DV R36), so a site the http rung reaches would fail here
            self._impersonate[name] = curl.Session(
                impersonate=chrome[-1] if chrome else "chrome", proxy=proxy,
                verify=certifi.where(),
            )  # fmt: skip
            self.jar.to_jar(self._impersonate[name].cookies)
        try:
            return self._impersonate[name].request(method, url, **kwargs)
        except Timeout as exc:
            raise requests.Timeout(str(exc)) from exc
        except RequestException as exc:
            raise requests.ConnectionError(str(exc)) from exc

    def _browser(self, rung: str, name: str = DIRECT, proxy: str | None = None):
        key = (rung, name)
        if key not in self._browsers:
            from ._browser import Browser

            if not self._browsers:
                atexit.register(self.close)
            har = None
            if self.har_dir and rung != "nodriver":  # nodriver has no HAR recorder
                stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
                har = self.har_dir / f"{stamp}-{rung}.har"
            try:
                self._browsers[key] = Browser(
                    rung, headless=self.headless, timeout=self.timeout, har=har, proxy=proxy
                )
            except ImportError as exc:
                raise RungUnavailable(f"{rung}: not installed (sdfetch[browser]): {exc}") from exc
            except Exception as exc:  # the browser itself didn't start
                raise RungUnavailable(f"{rung}: couldn't start: {exc}") from exc
            self._browsers[key].add_cookies(self.jar.to_playwright())
        return self._browsers[key]

    # Cookies -------------------------------------------------------------

    def add_cookies(self, cookies: list[dict]) -> None:
        """Merge cookies into the jar, save it, and give them to every open client."""
        if not self.jar.merge(cookies):
            return
        self.jar.save()
        self.jar.to_jar(self.session.cookies)
        for s in self._impersonate.values():
            self.jar.to_jar(s.cookies)
        for b in self._browsers.values():
            b.add_cookies(self.jar.to_playwright())

    def _collect_cookies(self, rung: str, route: str) -> None:
        """The cookies the client that just answered holds, into the jar."""
        name = self.config.resolve_route(route)[0]
        if rung == "http":
            found = CookieJar.from_cookiejar(self.session.cookies)
        elif rung == "impersonate" and name in self._impersonate:
            found = CookieJar.from_cookiejar(self._impersonate[name].cookies.jar)
        elif (rung, name) in self._browsers:
            found = self._browsers[(rung, name)].cookies()
        else:
            return
        self.add_cookies(found)

    # Requests ------------------------------------------------------------

    def _wait(self, state: RetryCallState) -> float:
        exc = state.outcome.exception() if state.outcome else None
        base = self.backoff * 2 ** (state.attempt_number - 1)
        if isinstance(exc, RetryableStatus):
            return retry_after(exc.response) or base
        if isinstance(exc, Blocked):
            return base * 4
        return base

    def _once(self, route: str, rung: str, method: str, url: str, check_blocked: bool,
              raise_errors: bool, **kwargs):  # fmt: skip
        settings = self._settings(url)
        if limit := settings.get("budget", self.budget):
            self._budget.spend(urlsplit(url).hostname or "", int(limit))
        self._pause(url)
        self.requests += 1
        if not get_strategy(rung).browser:
            kwargs.setdefault("timeout", self.timeout)
            if extra := settings.get("headers"):
                kwargs["headers"] = {**extra, **(kwargs.get("headers") or {})}
        resp = self._send(route, rung, method, url, **kwargs)
        phrases = [*self.block_phrases, *settings.get("block_phrases", [])]
        if check_blocked and (reason := blocked(resp, phrases)):
            raise Blocked(url, f"{reason} ({rung})", resp.status_code)
        if resp.status_code in RETRY_STATUSES:
            raise RetryableStatus(resp)
        if resp.status_code >= 400 and raise_errors:
            raise http_error(resp)
        return resp

    def _on_rung(self, route: str, rung: str, method: str, url: str, check_blocked: bool,
                 retry_blocked: bool, raise_errors: bool, **kwargs):  # fmt: skip
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
                    return self._once(
                        route, rung, method, url, check_blocked, raise_errors, **kwargs
                    )
        except RetryableStatus as exc:
            if not raise_errors:
                return exc.response  # still failing after the retries; the caller decides
            raise http_error(exc.response) from exc
        raise AssertionError("unreachable")

    def request(
        self,
        method: str,
        url: str,
        *,
        check_blocked: bool = True,
        raise_errors: bool | None = None,
        _relogged: bool = False,
        **kwargs,
    ):
        """One request, with the pause, retries and fallbacks, not stored. Returns a
        requests-like response (requests, curl_cffi, a browser's or a registered strategy's).
        Raises Blocked when the last attempt is blocked too. A status that isn't retried, or
        still fails after the retries, raises requests.HTTPError, or with raise_errors=False
        is returned for the caller to check. Strategies and routes that aren't available are
        skipped when there's something else to try. A response that looks logged out, for a
        host with a registered login, logs in once and retries (LoginRequired if that fails)."""
        raise_errors = self.raise_errors if raise_errors is None else raise_errors
        settings = self._settings(url)
        check_window(
            self.clock(),
            settings.get("hours", self.hours),
            settings.get("days", self.days),
            settings.get("tz", self.tz),
        )
        attempts = self._attempts(method, url)
        host = urlsplit(url).hostname or ""
        routes_tried = list(dict.fromkeys(r for r, _ in attempts))
        last: Exception | None = None
        dead_route = None
        for route, rung in attempts:
            if route == dead_route:
                continue
            try:
                resp = self._on_rung(
                    route, rung, method, url, check_blocked, retry_blocked=len(attempts) == 1,
                    raise_errors=raise_errors, **kwargs,
                )  # fmt: skip
            except Blocked as exc:
                last = exc
                logger.info("%s: blocked on %s via %s", redact(url), rung, route)
                continue
            except RungUnavailable as exc:
                if len(attempts) == 1:
                    raise
                logger.warning("%s: %s (via %s)", redact(url), exc, route)
                last = last if isinstance(last, Blocked) else exc
                continue
            except (requests.ConnectionError, requests.Timeout) as exc:
                # The site or the route is unreachable after the retries: another strategy on
                # the same route won't help, but another route might
                if len(routes_tried) == 1:
                    raise
                logger.warning("%s: route %s failed: %s", redact(url), route, exc)
                last = last if isinstance(last, Blocked) else exc
                dead_route = route
                continue
            name = self.config.resolve_route(route)[0]
            self.last_via, self.last_route = rung, name
            if self.memory and self._settings(url).get("strategy", self.strategy) == "auto":
                self.memory.record(host, rung, route)
            self.last_warc = None
            if self.warc_dir:
                if self._warc is None:
                    from ._warc import WarcWriter

                    self._warc = WarcWriter(self.warc_dir)
                    atexit.register(self.close)
                self.last_warc = (self._warc.path, self._warc.write(method, url, resp, rung))
            if rung not in self.clients:
                self._collect_cookies(rung, route)
            login = login_for(host)
            phrases = [
                *self.logged_out,
                *settings.get("logged_out", []),
                *(login[1] if login else []),
            ]
            if not self._logging_in and phrases and looks_logged_out(resp, phrases):
                if _relogged or login is None:
                    raise LoginRequired(
                        f"{redact(url)}: logged out" + ("" if login else "; no login registered")
                    )
                logger.info("%s: logged out; logging in", redact(url))
                self._logging_in = True
                try:
                    login[0](self, url)
                finally:
                    self._logging_in = False
                return self.request(
                    method, url, check_blocked=check_blocked, raise_errors=raise_errors,
                    _relogged=True, **kwargs,
                )  # fmt: skip
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
        resp = self.request(method, url, raise_errors=True, **kwargs)  # never store an error page
        where = {"via": self.last_via}
        if self.last_route != DIRECT:
            where["route"] = self.last_route  # a name from the config, never a proxy URL
        if self.last_warc:
            path, offset = self.last_warc
            root = self.store.root.resolve()
            shown = path.resolve()
            shown = shown.relative_to(root) if shown.is_relative_to(root) else shown
            where |= {"warc": shown.as_posix(), "warc_offset": offset}
        self.store.save(
            name,
            resp.content,
            url,
            method=method,
            status=resp.status_code,
            note={**where, **(note or {})},
            allow_empty=allow_empty,
        )
        return resp.content

    def text(self, url: str, name: str, **kwargs) -> str:
        """fetch(), decoded as UTF-8 (undecodable bytes replaced)."""
        return self.fetch(url, name, **kwargs).decode("utf-8", "replace")
