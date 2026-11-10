"""Shared test setup for every package, pipeline and the app."""

import pytest


def _drop_response_cookies(response):
    headers = response.get("headers", {})
    for name in [h for h in headers if h.lower() == "set-cookie"]:
        del headers[name]
    return response


@pytest.fixture(scope="session")
def vcr_config():
    """Recorded HTTP (pytest-recording): readable bodies, and nothing private or session-bound
    kept in a cassette (request credentials, response cookies)."""
    return {
        "decode_compressed_response": True,
        "filter_headers": ["authorization", "cookie", "set-cookie", "x-api-key"],
        "filter_query_parameters": ["key", "api_key", "token"],
        "before_record_response": _drop_response_cookies,
    }
