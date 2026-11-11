"""The local governments whose records platforms the generic sources read: each one's name,
place (fully qualified GEOID), website, and the platforms it publishes on, with what each
platform needs to find it (a site's base URL, a CivicClerk tenant, a Thrillshare site ID).

A generic source (civicplus-agendas, civicclerk-meetings, …) reads every government listed
for its platform; adding a government is one entry here."""

from __future__ import annotations

GOVERNMENTS = [
    {
        "name": "Lawrence County",
        "place": "0500000US46081",
        "site": "https://www.lawrence.sd.us",
        "platforms": {"civicplus": "https://www.lawrence.sd.us"},
    },
    {
        "name": "City of Spearfish",
        "place": "1600000US4660020",
        "site": "https://www.spearfish.gov",
        "platforms": {"civicplus": "https://www.spearfish.gov", "civicclerk": "spearfishsd"},
    },
    {
        "name": "Spearfish School District 40-2",
        "place": "9700000US4666930",
        "site": "https://www.spearfish.k12.sd.us",
        "platforms": {"thrillshare": "547213"},
    },
]


def using(platform: str) -> list[dict]:
    """The governments that publish on a platform, each with its platform setting."""
    return [{**g, "setting": g["platforms"][platform]} for g in GOVERNMENTS
            if platform in g["platforms"]]  # fmt: skip
