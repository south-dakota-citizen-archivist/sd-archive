"""County map apps: the hosted map and property-record sites many counties publish their GIS
data on, one app per county or city on a shared host.

Nothing here makes requests: the apps are known from the host's app picker, saved from a
browser (`clients.parse_apps`), and linked to by their keys.
"""

BASE_URL = "https://beacon.schneidercorp.com"  # the apps' host
