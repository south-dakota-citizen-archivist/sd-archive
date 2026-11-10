"""HAR: what a browser asked for while a page loaded, for finding the API behind it.

    with sdfetch.browser("stealth", har="search.har") as b:
        b.get("https://example.gov/search?q=smith")
    for r in sdfetch.har_requests("search.har"):
        print(r["method"], r["status"], r["mime"], r["url"])

or from the shell: `python -m sdfetch.har search.har` (add `--all` for every request).
A page that builds its table from JSON usually shows the JSON request here, and that URL can
then be fetched over plain HTTP instead of driving a browser.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

# What isn't data: the page itself, its code, styles, pictures and fonts
NOT_DATA = ("text/html", "javascript", "text/css", "image/", "font/", "video/", "audio/")
DATA = ("json", "xml", "csv", "text/plain", "x-www-form-urlencoded")


def har_requests(path: str | Path, all: bool = False) -> list[dict]:  # noqa: A002
    """The data requests in a HAR file (JSON, XML, CSV, XHR and fetch calls), or every request
    with all=True: method, url, status, mime, size, resource type and any POST body."""
    log = json.loads(Path(path).read_text())["log"]
    out = []
    for e in log.get("entries", []):
        req, resp = e["request"], e["response"]
        mime = (resp.get("content", {}).get("mimeType") or "").lower()
        kind = e.get("_resourceType", "")
        is_data = kind in ("xhr", "fetch") or (
            any(d in mime for d in DATA) and not any(n in mime for n in NOT_DATA)
        )
        if not (all or is_data):
            continue
        out.append(
            {
                "method": req["method"],
                "url": req["url"],
                "status": resp.get("status"),
                "mime": mime,
                "size": resp.get("content", {}).get("size"),
                "type": kind,
                "post": (req.get("postData") or {}).get("text"),
            }
        )
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="List the data requests in a HAR file")
    parser.add_argument("har")
    parser.add_argument("--all", action="store_true", help="every request, not just data")
    args = parser.parse_args()
    for r in har_requests(args.har, all=args.all):
        post = f"  POST {r['post'][:80]}" if r["post"] else ""
        print(f"{r['method']:<5} {r['status']} {r['mime'][:28]:<28} {r['url']}{post}")


if __name__ == "__main__":
    main()
