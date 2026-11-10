"""Platform adapters: plain-Python clients for the systems GIS data is published on.

Nothing in here imports Django, so adapters stay easy to test with fake servers.
"""

# Default request headers (shared with sdfetch)
from sdfetch import BROWSER_HEADERS, USER_AGENT  # noqa: F401
