import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

import keyring
import pytest
from keyring.backend import KeyringBackend

import sdfetch
from sdfetch import credentials

LOGIN_FORM = b"""<html><body><form method="post" action="/login">
<input type="hidden" name="__VIEWSTATE" value="vs123">
<input type="text" name="email"><input type="password" name="password">
<button type="submit">Sign in</button>
</form></body></html>"""


class Portal(BaseHTTPRequestHandler):
    logins = 0

    def log_message(self, *args):
        pass

    def _send(self, body, headers=None, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "text/html")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        cookie = self.headers.get("Cookie") or ""
        if self.path == "/login":
            return self._send(LOGIN_FORM)
        if self.path == "/private":
            if "session=good" in cookie:
                return self._send(b"<p>secret records</p>")
            return self._send(b"<p>Please sign in to continue</p>")
        if self.path == "/setcookie":
            return self._send(b"ok", {"Set-Cookie": "visited=1; Path=/; Max-Age=3600"})
        if self.path == "/echo":
            return self._send(cookie.encode())
        self._send(b"no", status=404)

    def do_POST(self):
        form = parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode())
        ok = (form.get("email") == ["me@example.org"] and form.get("password") == ["hunter2"]
              and form.get("__VIEWSTATE") == ["vs123"])  # fmt: skip
        Portal.logins += 1
        if ok:
            return self._send(b"<p>welcome</p>", {"Set-Cookie": "session=good; Path=/; HttpOnly"})
        self._send(b"<p>Please sign in: wrong password</p>")


@pytest.fixture(scope="module")
def portal():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Portal)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


class MemoryKeyring(KeyringBackend):
    priority = 1

    def __init__(self):
        self.entries = {}

    def get_password(self, service, username):
        return self.entries.get((service, username))

    def set_password(self, service, username, password):
        self.entries[(service, username)] = password

    def delete_password(self, service, username):
        del self.entries[(service, username)]


@pytest.fixture(autouse=True)
def memory_keyring():
    """Never the real keychain in tests."""
    before = keyring.get_keyring()
    keyring.set_keyring(MemoryKeyring())
    yield
    keyring.set_keyring(before)
    sdfetch.unregister_login("127.0.0.1")


def fetcher(tmp_path, **kwargs):
    kwargs.setdefault("cookies", tmp_path / "cookies.json")
    kwargs.setdefault("strategy", "http")
    return sdfetch.Fetcher(tmp_path / "store", memory=False, delay=0, **kwargs)


def test_credentials(tmp_path):
    credentials.set("portal.example.gov", "me@example.org", "hunter2")
    assert credentials.get("portal.example.gov") == ("me@example.org", "hunter2")
    credentials.delete("portal.example.gov")
    with pytest.raises(credentials.NoCredentials, match="credentials set portal.example.gov"):
        credentials.get("portal.example.gov")


def test_cookies_last_across_runs(tmp_path, portal):
    fetcher(tmp_path).request("GET", f"{portal}/setcookie")
    path = tmp_path / "cookies.json"
    assert path.stat().st_mode & 0o777 == 0o600
    later = fetcher(tmp_path)  # a new run
    assert later.request("GET", f"{portal}/echo").content == b"visited=1"


def test_impersonate_shares_the_jar(tmp_path, portal):
    fetcher(tmp_path).request("GET", f"{portal}/setcookie")
    f = fetcher(tmp_path, strategy="impersonate")
    assert f.request("GET", f"{portal}/echo").content == b"visited=1"


def test_form_login_then_retry(tmp_path, portal):
    credentials.set("127.0.0.1", "me@example.org", "hunter2")
    sdfetch.register_login(
        "127.0.0.1",
        sdfetch.form_login(f"{portal}/login", user_field="email"),
        logged_out=["Please sign in"],
    )
    Portal.logins = 0
    f = fetcher(tmp_path)
    assert f.fetch(f"{portal}/private", "private.html") == b"<p>secret records</p>"
    assert Portal.logins == 1
    # The session is in the jar: a later run doesn't log in again
    assert fetcher(tmp_path).request("GET", f"{portal}/private").content == b"<p>secret records</p>"
    assert Portal.logins == 1


def test_wrong_password_raises(tmp_path, portal):
    credentials.set("127.0.0.1", "me@example.org", "wrong")
    sdfetch.register_login("127.0.0.1", sdfetch.form_login(f"{portal}/login", user_field="email"),
                           logged_out=["Please sign in"])  # fmt: skip
    with pytest.raises(sdfetch.LoginRequired):
        fetcher(tmp_path).request("GET", f"{portal}/private")


def test_logged_out_without_a_login(tmp_path, portal):
    f = fetcher(tmp_path, logged_out=["Please sign in"])
    with pytest.raises(sdfetch.LoginRequired, match="no login registered"):
        f.request("GET", f"{portal}/private")


def test_jar_drops_expired(tmp_path):
    jar = sdfetch.CookieJar(tmp_path / "j.json")
    jar.merge([{"name": "a", "value": "1", "domain": "x.gov", "expires": 1},
               {"name": "b", "value": "2", "domain": "x.gov", "expires": None}])  # fmt: skip
    assert [c["name"] for c in jar] == ["b"]


@pytest.mark.browser
def test_browser_shares_the_jar(tmp_path, portal):
    with fetcher(tmp_path) as f:
        f.request("GET", f"{portal}/setcookie")
        f.strategy = "stealth"
        assert "visited=1" in f.request("GET", f"{portal}/echo").text


@pytest.mark.browser
def test_browser_login(tmp_path, portal):
    """The browser fills in and submits the form; its session cookie then serves plain HTTP."""
    credentials.set("127.0.0.1", "me@example.org", "hunter2")
    login = sdfetch.browser_login(
        f"{portal}/login", done_when="welcome", strategy="stealth", headless=True, timeout=30,
        user_selector="input[name=email]", pass_selector="input[name=password]",
        submit_selector="button[type=submit]",
    )  # fmt: skip
    sdfetch.register_login("127.0.0.1", login, logged_out=["Please sign in"])
    with fetcher(tmp_path) as f:
        assert f.request("GET", f"{portal}/private").content == b"<p>secret records</p>"
        assert f.last_via == "http"
    assert any(c["name"] == "session" for c in sdfetch.CookieJar(tmp_path / "cookies.json"))
