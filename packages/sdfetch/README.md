# sdfetch

Fetching with a raw store: HTTP with retries and per-host pauses, curl_cffi and browser fallbacks, a manifest of everything fetched, checkpoints, WARC and HAR recording, and the Wayback Machine. Plain Python, no Django.

```python
import sdfetch

f = sdfetch.Fetcher(".cache/elections/raw/3", delay=1.0)  # a Store, or a folder for one
html = f.text("https://electionresults.sd.gov/default.aspx?eid=3", "pages/default_eid=3.html")
```

## Fetcher

- `fetch(url, name)` returns the stored copy when there is one, and otherwise fetches and stores it. `refresh=True` fetches again. `data=` makes it a POST. `request()` sends without storing.
- **Retries** connection errors and HTTP 429/5xx with backoff, honoring `Retry-After`.
- **Pauses** between requests to the same host: `delay`, or per host with `delays={"host": seconds}`. The pause is shared by every Fetcher in the process.
- **Falls back** when a response isn't the content (an interstitial or error page, `Blocked`). It tries plain HTTP, then curl_cffi (`impersonate`), then a browser, stopping at the first method that gets the page.
  - The method that worked is remembered per host in `~/.cache/sdfetch/hosts.json` (`$SDFETCH_CACHE`), and the cheaper one below it is tried again weekly.
  - `strategy=` pins a method, and `max_strategy=` caps the fallback. POSTs stop at curl_cffi.
  - The manifest records which method fetched each file (`via`).
- **Errors** raise `requests.HTTPError`, or come back as responses with `raise_errors=False`. `fetch()` never stores an error page.
- Default headers are a desktop browser's (`BROWSER_HEADERS`). `user_agent=` and `headers=` override them.

## Routes

A route is where requests leave from: `direct`, or a proxy (`http://`, `https://`, `socks5://`). Give routes by name or URL with `Fetcher(routes=["direct", "vpn"])`, or per site in the local config.
- **Order:** a request tries each strategy on the first route, then each on the next.
- **Unreachable routes:** a route that can't connect at all is skipped.
- **Memory:** what worked is remembered per host, as a route and a strategy.
- **Coverage:** every strategy honors the route: requests, curl_cffi, the browsers, and registered strategies.
- **Manifest:** it records the route's name, never its URL.

## Local config

`~/.config/sdfetch/hosts.toml`, or the file `$SDFETCH_CONFIG` names, sets named routes and per-site settings. For a matching host (or its subdomains) these override what the code passes to `Fetcher`, so they can change without editing code.

```toml
[routes]
vpn = "socks5://127.0.0.1:1080"

[defaults]
routes = ["direct"]

[hosts."example.gov"]
strategy = "auto"            # or a strategy name
max_strategy = "patchright"
routes = ["direct", "vpn"]
delay = 3.0
headers = { Referer = "https://example.gov/" }
block_phrases = ["temporarily unavailable"]
```

Keep it readable only by you (`chmod 600`), since route URLs can include credentials.

## Adding a strategy

```python
def send(method, url, *, route, timeout, **kwargs):
    # return a requests-like response: status_code, headers, content, url
    ...


sdfetch.register_strategy("relay", send, after="impersonate")  # browser=True if it only GETs
```

A registered strategy takes its place in `sdfetch.ladder()`, gets the route as a proxy URL (or `None` for direct), and is used like the built-in ones.

## Browsers (`browser` extra)

```python
with sdfetch.browser("stealth") as b:  # or another of sdfetch.STRATEGIES
    resp = b.get("https://example.org/search?q=smith")
    b.page.click("text=Next")  # the Playwright page, for clicking and forms
```

The Playwright-based backends use the installed Google Chrome if there is one, and otherwise Playwright's Chromium. Camoufox downloads its own browser once (`uv run python -m camoufox fetch`, about 2.4 GB). A Fetcher's browsers stay open until `close()`, or until its `with` block ends.

## Store

Raw files by name, each written whole or not at all, plus `manifest.jsonl`: URL, method, status, bytes, SHA-256, time, and any `note`. A refresh that comes back empty keeps the stored copy (`EmptyResponse`). `blob()` stores by SHA-256 instead of by name.

## WARC and HAR

- `Fetcher(warc=True)` (the `warc` extra) also writes every successful response to `warc/<time>-<pid>.warc.gz` beside the store:
  - HTTP responses as `response` and `request` records
  - browser pages as `resource` records (the page after its scripts ran)

  Each manifest line gives its WARC file and offset.
- `Fetcher(har=True)`, or `browser(..., har="x.har")`, records everything a browser requests. `sdfetch.har_requests("x.har")`, or `uv run python -m sdfetch.har x.har`, lists the data requests (JSON, XML, CSV, XHR and fetch calls), which shows the API behind a page.

## Wayback Machine

```python
from sdfetch import wayback

f = wayback.fetcher(".cache/permits")  # 3 seconds between requests
for snap in wayback.snapshots(PAGE, start="2019"):
    html = wayback.fetch_snapshot(snap, f)  # the bytes the site served; cite snap.url
```

- `snapshots()` reads the CDX index, a page at a time. It keeps only status 200 by default, can filter by date and type, and `collapse="digest"` drops captures identical to the one before. Each index page is stored.
- `fetch_snapshot()` stores a capture at `wayback/<host>/<timestamp>.<ext>`, with the capture time and original URL in the manifest.
- `save_page(url)` requests a new capture (Save Page Now).

## Also

- `checkpoint(path)`: finished units and a cursor in a JSON file, rewritten after each `mark()`, so an interrupted run picks up where it stopped.
- `ensure_free(path, gb=50)`: raises `DiskSpaceLow` when the disk is nearly full.

## Use from another repo

```toml
[project]
dependencies = ["sdfetch[impersonate]"]

[tool.uv.sources]
sdfetch = { git = "https://github.com/south-dakota-citizen-archivist/sd-archive", subdirectory = "packages/sdfetch", tag = "sdfetch-v0.2.0" }
```

Extras: `impersonate` (curl_cffi), `browser` (Playwright and the other browser backends), `warc` (warcio).
