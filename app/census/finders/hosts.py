"""Find services directories on South Dakota governments' own web domains.

Domains come from CISA's list of .gov domains (those registered in South Dakota), Wikidata's
official websites for South Dakota's counties and places, and the hosts of sources already
in the census. For each domain the finder tries:

- the host names certificate logs (crt.sh) show under it that look like GIS hosts. Many
  governments use one wildcard certificate, which hides its host names, so this finds some
  and misses others;
- the usual GIS host names (gis., maps., arcgis. …), plus the domain itself and www.

A domain that answers for any made-up name (wildcard DNS) only gets its certificate-log
names, www. and itself. Every name that resolves is checked at the usual directory paths.
"""

from __future__ import annotations

import csv
import io
import logging
import re
import socket
import uuid
from collections.abc import Callable
from urllib.parse import urlsplit

from . import Getter, Lead

logger = logging.getLogger(__name__)

NAME = "Government domains"
CISA = "https://raw.githubusercontent.com/cisagov/dotgov-data/main/current-full.csv"
WIKIDATA = "https://query.wikidata.org/sparql"
# Counties (FIPS 6-4) and places (FIPS 55-3) in South Dakota with an official website
WIKIDATA_QUERY = """SELECT ?item ?code ?site WHERE {
  { ?item wdt:P882 ?code } UNION { ?item wdt:P774 ?code }
  FILTER(STRSTARTS(?code, "46"))
  ?item wdt:P856 ?site }"""
CRT = "https://crt.sh/"
GUESSES = ("gis", "maps", "map", "arcgis", "gisweb", "webgis", "geo", "gisportal", "mapping",
           "ags", "agsweb", "portal", "server", "data")  # fmt: skip
GIS_NAME = re.compile(r"(^|[.-])(gis|maps?|arcgis|geo\w*|webgis|gisweb|mapping|ags\w*|agol"
                      r"|portal|server|data)\d*([.-]|$)", re.IGNORECASE)  # fmt: skip
# Hosting platforms and other sites that aren't a government's own domain
NOT_OWN = re.compile(
    r"(^|\.)(arcgis\.com|schneidercorp\.com|civicplus\.com|municodeweb\.com|wixsite\.com"
    r"|squarespace\.com|godaddysites\.com|weebly\.com|google\.com|facebook\.com"
    r"|wordpress\.com|blogspot\.com|wikipedia\.org|github\.io|revize\.com)$",
    re.IGNORECASE,
)
PATHS = ("arcgis/rest/services", "server/rest/services", "rest/services")


def base(host: str) -> str:
    host = host.lower().strip(".")
    return host[4:] if host.startswith("www.") else host


def cisa_domains(get: Getter) -> dict[str, str]:
    rows = csv.DictReader(io.StringIO(get.text(CISA)))
    return {r["Domain name"].lower(): f"CISA .gov list: {r['Organization name']}"
            for r in rows if (r.get("State") or "").strip().upper() == "SD"}  # fmt: skip


def wikidata_domains(get: Getter) -> dict[str, str]:
    answer = get.json(WIKIDATA, {"query": WIKIDATA_QUERY, "format": "json"},
                      headers={"Accept": "application/sparql-results+json"})  # fmt: skip
    found = {}
    for row in answer["results"]["bindings"]:
        host = urlsplit(row["site"]["value"]).hostname
        if host and not NOT_OWN.search(host):
            found[base(host)] = f"Wikidata: official website ({row['code']['value']})"
    return found


def source_domains() -> dict[str, str]:
    from census.models import Source

    found = {}
    for url in Source.objects.values_list("url", flat=True):
        host = urlsplit(url).hostname or ""
        if host and not NOT_OWN.search(host):
            labels = host.split(".")
            keep = 3 if host.endswith(".sd.us") or host.endswith(".sd.gov") else 2
            found[".".join(labels[-keep:])] = "a source's host"
    return found


def cert_names(get: Getter, domain: str) -> set[str]:
    """GIS-looking host names certificate logs show under the domain."""
    try:
        rows = get.json(CRT, {"q": f"%.{domain}", "output": "json"}, timeout=120)
    except Exception as e:
        logger.info("crt.sh %s: %s", domain, e)
        return set()
    names = set()
    for row in rows:
        for name in str(row.get("name_value", "")).lower().split("\n"):
            name = name.strip().lstrip("*.")
            if name.endswith(domain) and GIS_NAME.search(name[: -len(domain)]):
                names.add(name)
    return names


def resolves(host: str) -> bool:
    try:
        return bool(socket.getaddrinfo(host, 443))
    except (OSError, UnicodeError):
        return False


def directory_at(get: Getter, host: str) -> tuple[str, int] | None:
    """The first services directory answering on the host: (root, services listed)."""
    for path in PATHS:
        root = f"https://{host}/{path}"
        try:
            data = get.json(root, {"f": "json"}, timeout=15)
        except Exception:
            continue
        if isinstance(data, dict) and ("services" in data or "folders" in data):
            return root, len(data.get("services") or []) + len(data.get("folders") or [])
    return None


def names_to_try(domain: str, certs: set[str], wildcard: bool) -> list[str]:
    guesses = [] if wildcard else [f"{g}.{domain}" for g in GUESSES]
    return list(dict.fromkeys([*sorted(certs), *guesses, f"www.{domain}", domain]))


def find(get: Getter | None = None, domains: dict[str, str] | None = None,
         resolve: Callable[[str], bool] = resolves) -> tuple[list[Lead], dict]:  # fmt: skip
    get = get or Getter(pause=1.0)
    if domains is None:
        domains = {}
        for name, lister in (("Wikidata", wikidata_domains), ("CISA", cisa_domains)):
            try:
                domains.update(lister(get))
            except Exception as e:
                logger.warning("%s domains: %s", name, e)
        domains.update(source_domains())
    leads, stats = [], {"domains": len(domains), "names_tried": 0, "resolved": 0,
                        "from_certificates": 0, "wildcard_domains": 0}  # fmt: skip
    for domain, why in sorted(domains.items()):
        wildcard = resolve(f"x{uuid.uuid4().hex[:12]}.{domain}")
        stats["wildcard_domains"] += wildcard
        certs = cert_names(get, domain)
        stats["from_certificates"] += len(certs)
        for host in names_to_try(domain, certs, wildcard):
            stats["names_tried"] += 1
            if not resolve(host):
                continue
            stats["resolved"] += 1
            found = directory_at(get, host)
            if found:
                root, n = found
                how = "certificate log" if host in certs else "usual host name"
                leads.append(Lead(root, NAME, title=host, evidence=[
                    {"domain": domain, "domain_from": why, "host": host, "host_from": how,
                     "listed": n}]))  # fmt: skip
    stats["roots"] = len(leads)
    return leads, stats
