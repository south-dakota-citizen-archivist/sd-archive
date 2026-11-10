import pytest

import sdfetch


@pytest.fixture(autouse=True)
def fresh_hosts(tmp_path, monkeypatch):
    """The per-host pause is shared across the process; start each test without one, and
    with its own host memory (hosts.json), never the real one in ~/.cache/sdfetch."""
    sdfetch._http._host_next.clear()
    monkeypatch.setenv("SDFETCH_CACHE", str(tmp_path / "sdfetch-cache"))
