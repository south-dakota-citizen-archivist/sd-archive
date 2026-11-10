# sdfetch

Fetching for the South Dakota archive. Plain Python, no Django. The design and the steps still to come (WARC/HAR, Wayback, the first release) are in [DESIGN.md](DESIGN.md).

```python
import sdfetch

f = sdfetch.Fetcher(".cache/elections/raw/3", delay=1.0)  # a Store, or a folder for one
html = f.text("https://electionresults.sd.gov/default.aspx?eid=3", "pages/default_eid=3.html")
```

- **`Fetcher`**: GET and POST with retries (connection errors and HTTP 429/5xx; `Retry-After` honored), a pause between requests to the same host (`delay`, or `delays={"host": seconds}`), and a check for challenge and block pages (`Blocked`). `fetch(url, name)` returns the stored copy when there is one; `refresh=True` fetches again. `request()` sends without storing.
- **Escalation** (`strategy="auto"`, the default): each request starts on the lightest rung that works for its host and climbs only when the response is a block page: `http` (requests) → `impersonate` (curl_cffi, Chrome's TLS fingerprint) → `stealth` (Playwright + playwright-stealth) → `patchright` → `camoufox` → `nodriver`. The rung that worked is remembered per host in `~/.cache/sdfetch/hosts.json` (`$SDFETCH_CACHE`), the rung below is retried once a week, and the manifest records each file's rung (`via`). POSTs stop at `impersonate`. Pin a rung with `strategy="patchright"`, or cap the climb with `max_strategy="impersonate"`. Install with the `impersonate` and `browser` extras.
- **`browser(strategy)`**: a running browser for work that needs one (clicking, forms): `b.get(url)` waits out a challenge page, and `b.page` is the Playwright page. The Chrome strategies drive the installed Google Chrome, so version and fingerprint are a real current Chrome. Camoufox downloads its own Firefox build once (`uv run python -m camoufox fetch`, about 2.4 GB). Browsers a Fetcher starts stay open until `close()` or the end of a `with` block.
- **`Store`**: raw files by name, each written whole or not at all, and `manifest.jsonl` (URL, method, status, bytes, SHA-256, time, plus any `note`). A refresh that comes back empty keeps the stored copy (`EmptyResponse`). `blob()` stores by SHA-256 instead.
- **`checkpoint(path)`**: finished units and a cursor in a JSON file, rewritten after each `mark()`, so an interrupted run picks up where it stopped.
- **`ensure_free(path, gb=50)`**: raises `DiskSpaceLow` before a big download when the disk is nearly full.

**Every request looks like desktop Chrome on a Mac** (`BROWSER_HEADERS`): nothing names the project, the org or a bot, and nothing goes out as `python-requests` or `Python-urllib`. sdgis and sdplaces send the same headers. Keep `CHROME` near the current stable version (157 as of 2026-11-10). A source can pass its own browser `user_agent`. Headers alone don't hide Python's TLS fingerprint; `impersonate=` (curl_cffi, step 2) does.
