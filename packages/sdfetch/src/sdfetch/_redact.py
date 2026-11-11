"""URLs as they're safe to show: credentials in the query or userinfo replaced."""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Query parameters that carry credentials (compared case-insensitively)
SECRET_PARAMS = frozenset({
    "api_key", "apikey", "key", "token", "access_token", "auth", "password", "secret",
    "client_secret", "signature", "sig",
})  # fmt: skip
REDACTED = "REDACTED"


def redact(url: object) -> str:
    """The URL with credential query parameters and any user:password replaced, so it can go
    in an error message, a log line or a manifest."""
    text = str(url)
    try:
        parts = urlsplit(text)
    except ValueError:
        return text
    netloc = parts.netloc
    if "@" in netloc:
        netloc = f"{REDACTED}@{netloc.rsplit('@', 1)[1]}"
    query = parts.query
    if query:
        pairs = parse_qsl(query, keep_blank_values=True)
        if any(k.lower() in SECRET_PARAMS for k, _ in pairs):
            query = urlencode([(k, REDACTED if k.lower() in SECRET_PARAMS else v)
                               for k, v in pairs])  # fmt: skip
    return urlunsplit((parts.scheme, netloc, parts.path, query, parts.fragment))
