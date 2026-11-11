"""Shared test setup for every package, pipeline and the app."""

import pytest

import sdfetch


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


@pytest.fixture(autouse=True)
def isolated_sdfetch(tmp_path, monkeypatch):
    """Every test gets its own sdfetch host memory and an empty private config, never the real
    ones (~/.cache/sdfetch/hosts.json, ~/.config/sdfetch/hosts.toml), and starts without the
    per-host pause that's shared across the process."""
    sdfetch._http._host_next.clear()
    monkeypatch.setenv("SDFETCH_CACHE", str(tmp_path / "sdfetch-cache"))
    monkeypatch.setenv("SDFETCH_CONFIG", str(tmp_path / "sdfetch-hosts.toml"))
