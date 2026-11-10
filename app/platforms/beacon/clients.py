"""Read the list of Beacon clients (apps) from the client picker on Beacon's home page.

Save the picker's HTML from a browser (the `#areaMenuContent` element); each option looks like

    <div id="client134BrookingsCountySD" data-appid="134">Brookings County, SD</div>

inside a `<div class="state-group" data-state="South Dakota">`.
"""

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urlencode

from platforms.beacon import BASE_URL


@dataclass(frozen=True)
class BeaconClient:
    app_id: int
    key: str  # the App= value in Beacon URLs, e.g. "BrookingsCountySD"
    name: str
    state: str

    def url(self, page_type: str = "Map") -> str:
        return app_url(self.key, page_type)


def app_url(key: str, page_type: str = "Map") -> str:
    """Link to an app by its key; Beacon resolves the page IDs itself."""
    return f"{BASE_URL}/Application.aspx?{urlencode({'App': key, 'PageType': page_type})}"


class _PickerParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.clients: list[BeaconClient] = []
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
            self.clients.append(BeaconClient(app_id, key, name, self.state))
            self.open = None


def parse_clients(html: str, state: str | None = None) -> list[BeaconClient]:
    parser = _PickerParser()
    parser.feed(html)
    seen, clients = set(), []
    for c in parser.clients:
        if c.app_id in seen or (state and c.state != state):
            continue
        seen.add(c.app_id)
        clients.append(c)
    return clients


def place_name(client: BeaconClient) -> str:
    """'Brookings County, SD' -> 'Brookings County'; 'City of Foley, AL' -> 'Foley'."""
    name = re.sub(r",\s*[A-Z]{2}$", "", client.name)
    return re.sub(r"^(City|Town|Village) of ", "", name)
