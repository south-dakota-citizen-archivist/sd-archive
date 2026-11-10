# sdfetch

Fetching for the South Dakota archive. Plain Python, no Django. The design and the steps still to come (browsers, curl_cffi, WARC, Wayback) are in [DESIGN.md](DESIGN.md).

```python
import sdfetch

f = sdfetch.Fetcher(".cache/elections/raw/3", delay=1.0)  # a Store, or a folder for one
html = f.text("https://electionresults.sd.gov/default.aspx?eid=3", "pages/default_eid=3.html")
```

- **`Fetcher`**: GET and POST with retries (connection errors and HTTP 429/5xx; `Retry-After` honored), a pause between requests to the same host (`delay`, or `delays={"host": seconds}`), and a check for challenge and block pages (`Blocked`). `fetch(url, name)` returns the stored copy when there is one; `refresh=True` fetches again. `request()` sends without storing.
- **`Store`**: raw files by name, each written whole or not at all, and `manifest.jsonl` (URL, method, status, bytes, SHA-256, time, plus any `note`). A refresh that comes back empty keeps the stored copy (`EmptyResponse`). `blob()` stores by SHA-256 instead.
- **`checkpoint(path)`**: finished units and a cursor in a JSON file, rewritten after each `mark()`, so an interrupted run picks up where it stopped.
- **`ensure_free(path, gb=50)`**: raises `DiskSpaceLow` before a big download when the disk is nearly full.

**Every request looks like desktop Chrome on a Mac** (`BROWSER_HEADERS`): nothing names the project, the org or a bot, and nothing goes out as `python-requests` or `Python-urllib`. sdgis and sdplaces send the same headers. Keep `CHROME` near the current stable version (157 as of 2026-11-10). A source can pass its own browser `user_agent`. Headers alone don't hide Python's TLS fingerprint; `impersonate=` (curl_cffi, step 2) does.
