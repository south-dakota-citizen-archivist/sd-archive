"""Read the list of map apps from the app picker on the apps' host's home page.

Save the picker's HTML from a browser (the `#areaMenuContent` element); each option looks like

    <div id="client134BrookingsCountySD" data-appid="134">Brookings County, SD</div>

inside a `<div class="state-group" data-state="South Dakota">`.
"""

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urlencode

from sdgis.mapapps import BASE_URL


@dataclass(frozen=True)
class MapApp:
    app_id: int
    key: str  # the App= value in the app's URLs, e.g. "BrookingsCountySD"
    name: str
    state: str

    def url(self, page_type: str = "Map") -> str:
        return app_url(self.key, page_type)


def app_url(key: str, page_type: str = "Map") -> str:
    """Link to an app by its key; the host resolves the page IDs itself."""
    return f"{BASE_URL}/Application.aspx?{urlencode({'App': key, 'PageType': page_type})}"


class _PickerParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.apps: list[MapApp] = []
        self.state = ""
        self.open: tuple[int, str] | None = None  # (app_id, key) of the option being read
        self.text: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag != "div":
            return
        if "data-state" in a:
            self.state = a["data-state"] or ""
        # "mru…" options repeat recently used apps; the "client…" ones are the full list
        m = re.fullmatch(r"client(\d+)(.+)", a.get("id") or "")
        if m and a.get("data-appid") == m[1]:
            self.open, self.text = (int(m[1]), m[2]), []

    def handle_data(self, data):
        if self.open:
            self.text.append(data)

    def handle_endtag(self, tag):
        if tag == "div" and self.open:
            app_id, key = self.open
            name = " ".join("".join(self.text).split()).replace(" ,", ",")
            self.apps.append(MapApp(app_id, key, name, self.state))
            self.open = None


def parse_apps(html: str, state: str | None = None) -> list[MapApp]:
    parser = _PickerParser()
    parser.feed(html)
    seen, apps = set(), []
    for c in parser.apps:
        if c.app_id in seen or (state and c.state != state):
            continue
        seen.add(c.app_id)
        apps.append(c)
    return apps


def place_name(client: MapApp) -> str:
    """'Brookings County, SD' -> 'Brookings County'; 'City of Foley, AL' -> 'Foley'."""
    name = re.sub(r",\s*[A-Z]{2}$", "", client.name)
    return re.sub(r"^(City|Town|Village) of ", "", name)
