"""Schneider Beacon (beacon.schneidercorp.com), the parcel and map portal many counties use.

Every Beacon URL, the JSON API and data extracts included, sits behind a Cloudflare bot
challenge, so nothing here makes requests. Beacon apps are known from the client picker
on Beacon's home page, saved from a browser.
"""

BASE_URL = "https://beacon.schneidercorp.com"
