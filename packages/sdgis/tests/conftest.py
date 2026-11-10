import pytest

from sdgis.arcgis import client
from sdgis.testing import FakeDirectory, FakeServer


@pytest.fixture
def server(monkeypatch) -> FakeServer:
    """Route all ArcGIS HTTP to a FakeServer. Tests can reassign attributes on it."""
    fake = FakeServer()
    monkeypatch.setattr(client, "make_session", lambda *a, **kw: fake)
    return fake


@pytest.fixture
def fake_directory(monkeypatch) -> FakeDirectory:
    fake = FakeDirectory()
    monkeypatch.setattr(client, "make_session", lambda *a, **kw: fake)
    return fake
