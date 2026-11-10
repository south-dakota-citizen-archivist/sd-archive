# sdfetch: design (2026-11-10)

How every pipeline and source gets things from the web: plain HTTP, HTTP that looks like a browser, real browsers, and the Wayback Machine. Whatever it gets is kept raw, with a manifest saying where each file came from. Plain Python, no Django. It replaces the separate fetchers in Spearfish (`scrapers/net.py`), spillarchive (`net.py`), the public records liberator (`_helpers.py`'s SOS session and block check), the elections pipeline (`capture.py`'s `Fetcher`) and the pistol permit Wayback code. It also replaces the playwright-stealth setup copied into five scrapers.

**Defaults:**
1. The raw store keeps **named files** (`pages/default_eid=3.html`), as the pipelines do now, because a named file can be checked by eye against its source. Content-addressed blobs (`blobs/ab/cd/<sha256>`, plan §3) are a second mode of the same store, for documents whose names aren't meaningful.
2. **All traffic looks native** (decided 2026-11-10): every request sends desktop Chrome's headers, with the version kept current, and never names the project, the org or a bot. That includes plain downloads, and GDAL's `/vsicurl/` reads (`GDAL_HTTP_USERAGENT`). `impersonate=` (curl_cffi, step 2) also matches Chrome's TLS fingerprint, and browsers run with stealth by default.
3. **One request at a time per host**, with a pause (1 second by default, set per host). Fetches run in sequence unless a caller chooses otherwise.

---

## 1. What it contains

| piece | what it does | step |
|---|---|---|
| `Fetcher` | GET and POST with retries and backoff (tenacity; honors `Retry-After` on 429 and 503), a per-host pause, and a block-page check. `fetch(url, name)` returns the stored copy if there is one, and otherwise fetches and stores it. | 1 |
| `Store` | a folder of raw files plus `manifest.jsonl` (URL, method, status, bytes, SHA-256, time, and any note the caller adds). Writes are atomic (`.partial`, then rename). **A refresh never replaces a good file with an empty or blocked one.** | 1 |
| `Blocked` | raised when a response is a challenge or block page (Cloudflare, Akamai, the SOS "we're sorry" page) instead of the content. It's retried with a longer wait, then raised for the caller to escalate, for example to a browser. | 1 |
| `checkpoint(path)` | a small JSON file of finished units and a cursor, written atomically after each unit. The app's `SourceState` will offer the same interface (plan §5.4). | 1 |
| `ensure_free(path, gb)` | refuses to start a big download when free disk space is below a threshold (plan §5.10). | 1 |
| `impersonate=` | curl_cffi's browser TLS and HTTP/2 fingerprints, for sites that block Python's. | 2 |
| `browser(strategy=…)` | one context manager around Playwright + playwright-stealth, patchright, Camoufox and nodriver/zendriver. Pages it saves go into the same store and manifest. | 2 |
| WARC and HAR | `warcio` writes every response to a WARC file as well (opt-in per source). HAR recording in the browser is for finding hidden APIs. | 3 |
| Wayback | CDX search for a URL's snapshots, fetching a snapshot's original bytes (`id_`), and Save Page Now. | 4 |

## 1a. Escalation: the lightest tool that works

A new source starts with plain HTTP and climbs only as far as it has to. The ladder:

| rung | what | when it's needed |
|---|---|---|
| `http` | requests, with Chrome's headers | most government sites and APIs |
| `impersonate` | curl_cffi, with Chrome's TLS and HTTP/2 fingerprint | sites that block Python's TLS handshake (some Cloudflare and Akamai setups) |
| `stealth` | Playwright + playwright-stealth | pages built by JavaScript, light bot checks |
| `patchright` | patched Chromium, no automation flags | Cloudflare Turnstile and similar challenges |
| `camoufox` | Firefox with a spoofed fingerprint | sites that still catch patched Chromium |
| `nodriver` | Chrome over CDP without WebDriver | the last resort |

- **`Fetcher(strategy="auto")`** (the default) climbs one rung whenever a fetch is `Blocked`: a challenge page, a 403 or 429 with a challenge, or an empty body where content is expected. It stops at the first rung that returns the content.
- **It remembers.** The rung that worked is saved per host (`~/.cache/sdfetch/hosts.json`), so later runs start there instead of climbing again. About once a week it retries the rung below, so a site that relaxes goes back to the cheaper tool.
- **A source can pin a rung** (`strategy="camoufox"`) when it's known, or cap the climb (`max_strategy="impersonate"`) for APIs where a browser makes no sense.
- **Every manifest line records the rung** that fetched the file (`"via": "patchright"`), so the record shows how each file was obtained.
- When the top rung is still blocked, `Blocked` reaches the caller and the source's checks report it. Nothing is stored, and the good copy is kept.

The add-source skill gets the same rule: try `http` first, and climb only when a fetch is blocked.

## 2. The interface

```python
import sdfetch

store = sdfetch.Store(".cache/elections/raw/3")
f = sdfetch.Fetcher(store, delay=1.0)

html = f.text("https://electionresults.sd.gov/default.aspx?eid=3", "pages/default_eid=3.html")
xlsx = f.fetch(url, "exports/Aurora.xlsx", data=form, note={"label": "Aurora"})  # a POST
f.fetch(
    url, "pages/live.html", refresh=True
)  # fetch again; the old copy stays if the new one is empty

resp = f.request("GET", url)  # not stored, for probes

cp = sdfetch.checkpoint(".cache/spills/checkpoint.json")
for unit in units:
    if cp.done(unit):
        continue
    ...
    cp.mark(unit)  # written now, so an interruption loses at most this unit

sdfetch.ensure_free(".cache", gb=50)
```

Per-host settings: `sdfetch.Fetcher(store, delays={"sdsos.gov": 3.0})`. Block checks can be extended per source: `Fetcher(store, block_phrases=[...])`.

## 3. Steps

1. **Core** (`Fetcher`, `Store`, `Blocked`, `checkpoint`, `ensure_free`) with tests against a local test server, and the elections pipeline switched to it.
2. ✅ 2026-11-10. **Looking like a browser, and escalation (§1a):** the `impersonate` rung through curl_cffi, the browser rungs behind `browser(strategy=…)`, `strategy="auto"` climbing on `Blocked`, the per-host memory, and `via` in the manifest. The five playwright-stealth scrapers (Spearfish, the liberator) switch over when their repos move onto sdfetch (step 5). A live check: plain HTTP with Chrome's headers got the Spearfish library's Cloudflare-fronted reports from the laptop, so that scraper may not need its browser at all.
3. ✅ 2026-11-10. **WARC and HAR.** `Fetcher(warc=True)` writes every successful response to `warc/<time>-<pid>.warc.gz` beside the store: `response` and `request` records for the HTTP rungs, and `resource` records for browser pages, since a browser page is the document after its scripts ran. Each record notes its rung, and each manifest line gives its WARC file and offset. `har=` records each browser's traffic, and `sdfetch.har_requests()` / `python -m sdfetch.har` list the data requests in it, which is how a page's hidden API is found.
4. ✅ 2026-11-10. **Wayback** (`sdfetch.wayback`):
   - `snapshots()`: the CDX index, paged by resume key, with filters for date, status (200 by default) and type, plus optional `collapse="digest"`. Each index page is stored.
   - `fetch_snapshot()`: the original bytes from the `id_` URL, with the capture time and original URL in the manifest.
   - `save_page()`: Save Page Now. It's built and tested against a fake server, but nothing calls it automatically, since it makes a public capture.

   The pistol permit code's needs are covered:
   - every capture in its `snapshots.json` (114) is in `snapshots()`
   - a raw capture holds the same figures as its CSV row
   - the index now has 76 captures newer than that list, plus 5 older ones it missed

   The repo itself switches when it moves into `pipelines/`.
5. ✅ 2026-11-10. **First release.** sd-archive is public on GitHub, with tags `sdfetch-v0.1.0` and `sdfetch-v0.1.1`. 0.1.1 lets sdfetch replace requests-style code: errors can come back as responses (`raise_errors=False`), `Blocked` is a `RequestException`, and auto mode skips rungs that aren't installed. Spearfish installs it as a git dependency pinned to the tag (whats-up-spearfish PR #7):
   - `scrapers/net.py` and spillarchive's `net.py` keep their APIs, on one Fetcher per thread capped at curl_cffi.
   - The library scraper starts on plain HTTP and can climb to the stealth browser.
   - A full run of all 59 sources matched the last scheduled run.

   To release again: bump `packages/sdfetch/pyproject.toml`, tag `sdfetch-vX.Y.Z`, push the tag, and change the tag in the user's `[tool.uv.sources]`.
