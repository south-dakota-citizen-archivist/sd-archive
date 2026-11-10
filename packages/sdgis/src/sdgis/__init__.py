"""Platform adapters: plain-Python clients for the systems GIS data is published on.

Nothing in here imports Django, so adapters stay easy to test with fake servers.
"""

# Requests look like a desktop browser (sdfetch); nothing names the project
from sdfetch import BROWSER_HEADERS, USER_AGENT  # noqa: F401
