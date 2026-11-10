"""Sessions: cookies kept across runs and strategies, and logins for portals.

    f = sdfetch.Fetcher(store, cookies="sos")      # ~/.cache/sdfetch/cookies/sos.json

    sdfetch.register_login(
        "portal.example.gov",
        sdfetch.form_login("https://portal.example.gov/login", user_field="email"),
        logged_out=["Please sign in", "/login"],
    )

Cookies: one jar per name, loaded into every strategy the Fetcher uses (requests, curl_cffi,
browsers) and saved after every successful response, including cookies a browser picked
up. The file is readable only by you; expired cookies are dropped when it's loaded.

Logins: when a response for a registered host looks logged out (a `logged_out` phrase in its
text or final URL), the Fetcher runs the host's login once and retries the request. If the
page still looks logged out, it raises LoginRequired. Usernames and passwords come from the
system keychain (sdfetch.credentials).

- form_login(): an ordinary login form, posted with its hidden fields (view state, CSRF).
- browser_login(): opens a visible browser at the login page, fills in the login if told
  where, and waits for a person to finish (a CAPTCHA, two-factor). The browser's cookies
  then go into the jar, so plain requests carry the session.
"""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Callable, Iterable
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests

from . import credentials
from ._ladder import cache_dir

# ---------------------------------------------------------------------------
# Cookies


class CookieJar:
    """Cookies as plain dicts (name, value, domain, path, expires, secure, httpOnly), keyed
    by (domain, path, name). path=None keeps them in memory only."""

    def __init__(self, value: bool | str | Path | None = None):
        if value is True:
            value = "default"
        if isinstance(value, str) and "/" not in value and not value.endswith(".json"):
            value = cache_dir() / "cookies" / f"{value}.json"
        self.path = Path(value).expanduser() if value else None
        self._cookies: dict[tuple, dict] = {}
        if self.path and self.path.exists():
            self.merge(json.loads(self.path.read_text()))

    def __len__(self) -> int:
        return len(self._cookies)

    def __iter__(self):
        return iter(self._cookies.values())

    def merge(self, cookies: Iterable[dict]) -> bool:
        """Add or update cookies, dropping expired ones; True if anything changed."""
        changed = False
        now = time.time()
        for c in cookies:
            key = (c["domain"], c.get("path") or "/", c["name"])
            expires = c.get("expires")
            if expires not in (None, -1) and float(expires) < now:
                changed |= self._cookies.pop(key, None) is not None
                continue
            entry = {
                "name": c["name"],
                "value": c["value"],
                "domain": c["domain"],
                "path": c.get("path") or "/",
                "expires": None if expires in (None, -1) else float(expires),
                "secure": bool(c.get("secure")),
                "httpOnly": bool(c.get("httpOnly")),
            }
            if self._cookies.get(key) != entry:
                self._cookies[key] = entry
                changed = True
        return changed

    def save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".partial")
        tmp.write_text(
            json.dumps(
                sorted(self._cookies.values(), key=lambda c: (c["domain"], c["name"])), indent=1
            )
        )
        tmp.chmod(0o600)
        tmp.replace(self.path)

    # From and to each client

    @staticmethod
    def from_cookiejar(jar) -> list[dict]:
        """From an http.cookiejar.CookieJar (requests, curl_cffi's .jar)."""
        return [
            {
                "name": c.name,
                "value": c.value or "",
                "domain": c.domain,
                "path": c.path,
                "expires": c.expires,
                "secure": c.secure,
                "httpOnly": c.has_nonstandard_attr("HttpOnly"),
            }
            for c in jar
        ]

    def to_jar(self, cookies) -> None:
        """Into a requests or curl_cffi cookie collection (anything with .set())."""
        for c in self._cookies.values():
            cookies.set(c["name"], c["value"], domain=c["domain"], path=c["path"],
                        secure=c["secure"])  # fmt: skip

    def to_playwright(self) -> list[dict]:
        return [
            {
                "name": c["name"],
                "value": c["value"],
                "domain": c["domain"],
                "path": c["path"],
                "expires": c["expires"] if c["expires"] is not None else -1,
                "secure": c["secure"],
                "httpOnly": c["httpOnly"],
            }
            for c in self._cookies.values()
        ]


# ---------------------------------------------------------------------------
# Logins


class LoginRequired(requests.RequestException):
    """Still logged out after logging in (or no login registered for the host)."""


_logins: dict[str, tuple[Callable, list[str]]] = {}


def register_login(host: str, handler: Callable, *, logged_out: Iterable[str] = ()) -> None:
    """handler(fetcher, url) logs in using the fetcher (whose cookies it shares). logged_out:
    phrases in a response's text, or parts of its final URL, that mean the session ended."""
    _logins[host.lower()] = (handler, list(logged_out))


def unregister_login(host: str) -> None:
    _logins.pop(host.lower(), None)


def login_for(host: str) -> tuple[Callable, list[str]] | None:
    """The login for host or the nearest parent domain registered."""
    host = (host or "").lower()
    matches = [k for k in _logins if host == k or host.endswith("." + k)]
    return _logins[max(matches, key=len)] if matches else None


def looks_logged_out(resp, phrases: Iterable[str]) -> bool:
    url = str(getattr(resp, "url", "") or "")
    text = (resp.content or b"")[:200_000].decode("utf-8", "replace")
    return any(p and (p in url or p in text) for p in phrases)


class _HiddenInputs(HTMLParser):
    def __init__(self):
        super().__init__()
        self.fields: dict[str, str] = {}
        self.action: str | None = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form" and self.action is None:
            self.action = a.get("action")
        if tag == "input" and (a.get("type") or "").lower() == "hidden" and a.get("name"):
            self.fields[a["name"]] = a.get("value") or ""


def form_login(
    login_url: str,
    *,
    user_field: str = "username",
    pass_field: str = "password",
    name: str | None = None,
    extra: dict | None = None,
) -> Callable:
    """A login handler for an HTML form: GET the page, then POST its hidden fields with the
    username and password from the keychain (stored under `name`, default the host)."""

    def handler(fetcher, url: str) -> None:
        username, password = credentials.get(name or urlsplit(login_url).hostname or "")
        page = fetcher.request("GET", login_url, raise_errors=True)
        form = _HiddenInputs()
        form.feed(page.content.decode("utf-8", "replace"))
        target = urljoin(str(page.url), form.action) if form.action else str(page.url)
        data = {**form.fields, **(extra or {}), user_field: username, pass_field: password}
        fetcher.request("POST", target, data=data, raise_errors=False)

    return handler


def browser_login(
    login_url: str,
    *,
    done_when: str,
    name: str | None = None,
    user_selector: str | None = None,
    pass_selector: str | None = None,
    submit_selector: str | None = None,
    strategy: str = "patchright",
    headless: bool = False,
    timeout: float = 600,
) -> Callable:
    """A login handler that opens a browser at login_url and waits (up to timeout seconds)
    until done_when appears in its URL or page. With selectors, it fills in the username and
    password from the keychain; anything else (a CAPTCHA, two-factor) is left to a person
    at the window. Its cookies then go into the Fetcher's jar."""

    def handler(fetcher, url: str) -> None:
        from ._browser import Browser

        b = Browser(strategy, headless=headless, timeout=60)
        try:
            b.add_cookies(fetcher.jar.to_playwright())
            b.page.goto(login_url)
            if user_selector and pass_selector:
                username, password = credentials.get(name or urlsplit(login_url).hostname or "")
                b.page.fill(user_selector, username)
                b.page.fill(pass_selector, password)
                if submit_selector:
                    b.page.click(submit_selector)
            if not headless:
                print(f"Finish logging in to {urlsplit(login_url).hostname} in the browser "
                      "window; it closes by itself.", file=sys.stderr)  # fmt: skip
            deadline = time.monotonic() + timeout
            while True:
                try:
                    if done_when in b.page.url or done_when in b.page.content():
                        break
                except Exception:  # noqa: BLE001 - the page is navigating
                    pass
                if time.monotonic() > deadline:
                    raise LoginRequired(f"login at {login_url} wasn't finished in time")
                b.page.wait_for_timeout(1000)
            fetcher.add_cookies(b.cookies())
        finally:
            b.close()

    return handler
