"""WARC: every response a Fetcher gets, in the standard web-archive format (needs `warc`).

One gzipped WARC file per Fetcher, `warc/<UTC time>-<pid>.warc.gz`. Each record is its own
gzip member and is flushed as it's written, so a run that's interrupted leaves a valid file
up to the last whole record.

- http and impersonate: a `response` record (status line, headers, body) and the `request`
  record that asked for it, linked by WARC-Concurrent-To. The body is stored decoded, so
  Content-Encoding is dropped and Content-Length set to the stored length.
- Browser rungs: a `resource` record. The page there is the document after its scripts ran
  (what the browser showed), not the bytes the server sent, so it isn't called a response.

Every record says which rung got it (`sdfetch-via`), and the manifest line for a stored
file says where its record is (`warc`, `warc_offset`).
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit

from ._ladder import BROWSER_RUNGS

DROP = {"content-encoding", "transfer-encoding", "content-length"}


class WarcWriter:
    def __init__(self, folder: str | Path):
        from warcio.warcwriter import WARCWriter

        stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
        self.path = Path(folder) / f"{stamp}-{os.getpid()}.warc.gz"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("ab")
        self._writer = WARCWriter(self._file, gzip=True)
        info = self._writer.create_warcinfo_record(
            self.path.name, {"software": "sdfetch", "format": "WARC File Format 1.1"}
        )
        self._writer.write_record(info)
        self._file.flush()

    def write(self, method: str, url: str, resp, via: str) -> int:
        """Record one response (and its request); returns the offset of the first record."""
        from warcio.statusandheaders import StatusAndHeaders

        offset = self._file.tell()
        headers = dict(resp.headers.items()) if resp.headers else {}
        via_header = {"sdfetch-via": via}
        if via in BROWSER_RUNGS:
            kind = headers.get("content-type") or headers.get("Content-Type") or "text/html"
            record = self._writer.create_warc_record(
                url,
                "resource",
                payload=BytesIO(resp.content),
                length=len(resp.content),
                warc_content_type=kind,
                warc_headers_dict=via_header,
            )
            self._writer.write_record(record)
            self._file.flush()
            return offset

        reason = getattr(resp, "reason", "") or ""
        kept = [(k, v) for k, v in headers.items() if k.lower() not in DROP]
        kept.append(("Content-Length", str(len(resp.content))))
        http = StatusAndHeaders(f"{resp.status_code} {reason}".strip(), kept, protocol="HTTP/1.1")
        response = self._writer.create_warc_record(
            url,
            "response",
            payload=BytesIO(resp.content),
            length=len(resp.content),
            http_headers=http,
            warc_headers_dict=via_header,
        )
        self._writer.write_record(response)

        req = getattr(resp, "request", None)
        req_headers = list(dict(getattr(req, "headers", None) or {}).items())
        body = getattr(req, "body", None) or b""
        if isinstance(body, str):
            body = body.encode()
        parts = urlsplit(url)
        target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        request = self._writer.create_warc_record(
            url,
            "request",
            payload=BytesIO(body),
            length=len(body),
            http_headers=StatusAndHeaders(
                f"{method} {target} HTTP/1.1", req_headers, is_http_request=True
            ),
            warc_headers_dict={
                **via_header,
                "WARC-Concurrent-To": response.rec_headers.get_header("WARC-Record-ID"),
            },
        )
        self._writer.write_record(request)
        self._file.flush()
        return offset

    def close(self) -> None:
        if not self._file.closed:
            self._file.close()
