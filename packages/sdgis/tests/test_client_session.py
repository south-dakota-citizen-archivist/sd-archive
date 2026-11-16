"""A host behind a bot check is asked again as Chrome, and from then on."""

from sdgis.arcgis import client


class FakeResponse:
    def __init__(self, status, text="", server=""):
        self.status_code, self.text, self.headers = status, text, {"server": server}


class FakeCurl:
    def __init__(self):
        self.asked = []

    def request(self, method, url, **kwargs):
        self.asked.append(url)
        return FakeResponse(200, '{"services": []}')


def test_a_bot_check_switches_the_host_to_chrome(monkeypatch):
    plain = []

    def answer(self, method, url, **kwargs):
        plain.append(url)
        if "guarded" in url:
            return FakeResponse(403, "<title>Just a moment...</title>", "cloudflare")
        return FakeResponse(200, "{}")

    monkeypatch.setattr("requests.Session.request", answer)
    monkeypatch.setattr(client, "IMPERSONATED_HOSTS", set())
    session = client.make_session()
    curl = session._curl = FakeCurl()
    assert session.get("https://guarded.example/rest/services").status_code == 200
    session.get("https://guarded.example/rest/services/A/MapServer")  # straight to Chrome
    session.get("https://open.example/rest/services")
    assert curl.asked == ["https://guarded.example/rest/services",
                          "https://guarded.example/rest/services/A/MapServer"]  # fmt: skip
    assert plain == ["https://guarded.example/rest/services", "https://open.example/rest/services"]
    assert sorted(client.IMPERSONATED_HOSTS) == ["guarded.example"]
