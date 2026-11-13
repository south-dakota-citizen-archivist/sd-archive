"""County map apps: the hosted map and property-record site many counties use.

- session: one Chrome window on an app's map page. Requests go out from the page itself:
  the map's own API (POST <api path>/<method>?QPS=…) as the map's scripts make it, and
  report pages by navigating to them, with pauses between requests and a back-off when a
  response is a block page.
- tiles: every feature of a layer, read by map extent: GetVectorLayer answers at most 500
  features for an extent, so an extent that comes back full is split in four and read again.
- parse: the map's configuration, the features (key, WKT geometry, tooltip), and report pages
  (each section's tables, the owners, the sections that need an account).
"""

BASE_URL = "https://beacon.schneidercorp.com"  # the apps' host
