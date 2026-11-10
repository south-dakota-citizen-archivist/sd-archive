"""Thin client for ArcGIS REST layer endpoints. Plain Python: no Django imports."""

import hashlib
import io
import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from itertools import batched
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from sdgis import BROWSER_HEADERS

logger = logging.getLogger(__name__)

# Feature queries can be large; metadata requests should answer quickly
TIMEOUT = 120
METADATA_TIMEOUT = 30
RETRIES = 3
DEFAULT_BATCH_SIZE = 1000
MAX_PAGES = 10_000


class ArcGISError(RuntimeError):
    """Raised when an ArcGIS server returns an error payload."""


def make_session(retries: int = RETRIES) -> requests.Session:
    retry = Retry(
        total=retries,
        backoff_factor=2,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
    )
    adapter = HTTPAdapter(max_retries=retry)
    session = requests.Session()
    session.headers.update(BROWSER_HEADERS)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def request(
    session: requests.Session, url: str, params: dict | None = None, timeout: float = TIMEOUT
) -> tuple[dict, requests.Response]:
    resp = session.get(url, params={**(params or {}), "f": "json"}, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    # ArcGIS reports most failures as HTTP 200 with an "error" object
    if "error" in data:
        err = data["error"]
        details = "; ".join(err.get("details") or [])
        raise ArcGISError(f"{url}: {err.get('code')} {err.get('message')} {details}".strip())
    return data, resp


def get_json(
    session: requests.Session, url: str, params: dict | None = None, timeout: float = TIMEOUT
) -> dict:
    return request(session, url, params, timeout)[0]


def get_metadata(session: requests.Session, layer_url: str, timeout: float = TIMEOUT) -> dict:
    return get_json(session, layer_url, timeout=timeout)


def get_count(
    session: requests.Session, layer_url: str, where: str = "1=1", timeout: float = TIMEOUT
) -> int:
    data = get_json(
        session, f"{layer_url}/query", {"where": where, "returnCountOnly": "true"}, timeout
    )
    return int(data["count"])


def get_ids(session: requests.Session, layer_url: str, where: str = "1=1") -> list[int]:
    data = get_json(session, f"{layer_url}/query", {"where": where, "returnIdsOnly": "true"})
    return sorted(data.get("objectIds") or [])


def object_id_field(metadata: dict) -> str | None:
    if metadata.get("objectIdField"):
        return metadata["objectIdField"]
    for field in metadata.get("fields") or []:
        if field.get("type") == "esriFieldTypeOID":
            return field["name"]
    return None


def last_edit(metadata: dict) -> datetime | None:
    """When the layer's data last changed, if the server says. Hosted layers usually do;
    ArcGIS Server map services usually don't."""
    info = metadata.get("editingInfo") or {}
    ms = info.get("dataLastEditDate") or info.get("lastEditDate")
    return datetime.fromtimestamp(ms / 1000, tz=UTC) if ms else None


def code_key(value) -> str:
    """Coded values as JSON object keys: 3, 3.0 and "3" all become "3"."""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value)


def coded_values(domain: dict | None) -> dict[str, str]:
    if not domain or domain.get("type") != "codedValue":
        return {}
    return {code_key(c["code"]): c["name"] for c in domain.get("codedValues") or []}


def describe_fields(metadata: dict) -> list[dict]:
    """Fields with their code labels: {"name", "type", "alias"}, plus
    - "codes": {code: label}, from the field's coded-value domain
    - "subtype_codes": {subtype: {code: label}}, where a subtype overrides the domain
    - "subtype_field": true on the field that holds the subtype, whose codes name the subtypes
    """
    subtype_field = (metadata.get("typeIdField") or "").lower()
    subtypes = metadata.get("types") or []
    described = []
    for f in metadata.get("fields") or []:
        field = {"name": f["name"], "type": f.get("type", ""), "alias": f.get("alias", "")}
        if codes := coded_values(f.get("domain")):
            field["codes"] = codes
        if subtypes and f["name"].lower() == subtype_field:
            field["codes"] = {code_key(t["id"]): t["name"] for t in subtypes}
            field["subtype_field"] = True
        by_subtype = {
            code_key(t["id"]): codes
            for t in subtypes
            if (codes := coded_values((t.get("domains") or {}).get(f["name"])))
        }
        if by_subtype:
            field["subtype_codes"] = by_subtype
        described.append(field)
    return described


def supports_pagination(metadata: dict) -> bool:
    return bool((metadata.get("advancedQueryCapabilities") or {}).get("supportsPagination"))


def to_frame(frames: list[pd.DataFrame]) -> gpd.GeoDataFrame:
    """Combine query results. Tables (no geometry) come back as plain DataFrames."""
    df = pd.concat(frames, ignore_index=True)
    if isinstance(frames[0], gpd.GeoDataFrame):
        return gpd.GeoDataFrame(df, crs=frames[0].crs)
    return gpd.GeoDataFrame(df)


def query_features(
    session: requests.Session, layer_url: str, params: dict
) -> tuple[pd.DataFrame, bool]:
    """Run one query; return its features (EPSG:4326) and whether the server truncated it."""
    data, resp = request(
        session, f"{layer_url}/query", {"outFields": "*", "outSR": "4326", **params}
    )
    truncated = bool(data.pop("exceededTransferLimit", False))
    # GDAL tries to page on its own when it sees exceededTransferLimit, which
    # fails on in-memory data, so hand it the payload without the flag
    content = json.dumps(data).encode() if truncated else resp.content
    return gpd.read_file(io.BytesIO(content)), truncated


def fetch_id_range(
    session: requests.Session, layer_url: str, where: str, oid_field: str, ids: list[int]
) -> list[pd.DataFrame]:
    """Fetch features whose IDs fall in [ids[0], ids[-1]], halving the range if truncated."""
    clause = f"({where}) AND {oid_field} >= {ids[0]} AND {oid_field} <= {ids[-1]}"
    gdf, truncated = query_features(session, layer_url, {"where": clause})
    if not truncated:
        return [gdf]
    if len(ids) == 1:
        raise ArcGISError(f"{layer_url}: server truncated a single-feature query")
    logger.debug("Batch of %d truncated by server; splitting", len(ids))
    mid = len(ids) // 2
    return fetch_id_range(session, layer_url, where, oid_field, ids[:mid]) + fetch_id_range(
        session, layer_url, where, oid_field, ids[mid:]
    )


def fetch_by_offset(
    session: requests.Session, layer_url: str, where: str, batch_size: int
) -> list[pd.DataFrame]:
    frames, offset = [], 0
    for _ in range(MAX_PAGES):
        gdf, truncated = query_features(
            session,
            layer_url,
            {"where": where, "resultOffset": offset, "resultRecordCount": batch_size},
        )
        frames.append(gdf)
        offset += len(gdf)
        if not truncated or gdf.empty:
            return frames
    # Most likely a server that claims pagination support but ignores resultOffset
    raise ArcGISError(f"{layer_url}: gave up after {MAX_PAGES} pages")


def _staged(staging: Path | None, key: str, fetch: Callable[[], list[pd.DataFrame]]):
    """fetch(), or the frames a previous run saved under key in staging. Each batch is written
    whole (to a temporary name, then renamed), so a stopped run never leaves half a batch.
    Pickled, not parquet, so a batch reads back exactly as it was downloaded."""
    if staging is None:
        return fetch(), False
    path = Path(staging) / f"{key}.pkl"
    if path.exists():
        return pd.read_pickle(path), True
    frames = fetch()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    pd.to_pickle(frames, tmp)
    tmp.replace(path)
    return frames, False


def fetch_features(
    session: requests.Session,
    layer_url: str,
    metadata: dict,
    where: str = "1=1",
    batch_size: int | None = None,
    staging: str | Path | None = None,
) -> gpd.GeoDataFrame:
    """Download every feature in a layer as a GeoDataFrame in EPSG:4326.

    Strategies, in order of preference:
    1. Object ID ranges: works whether or not the server supports pagination,
       and stays stable if rows change mid-scrape.
    2. resultOffset paging, for layers without an object ID field.
    3. A single query. If the server truncates it, count validation will catch it.

    staging: a folder where each object-ID batch is saved as it arrives. A run that stops
    partway and starts again reuses the batches already saved (a batch is named for its IDs
    and the filter, so a changed layer fetches the batches that changed). The caller deletes
    the folder once the download is used.
    """
    batch_size = batch_size or metadata.get("maxRecordCount") or DEFAULT_BATCH_SIZE
    oid_field = object_id_field(metadata)

    if oid_field:
        ids = get_ids(session, layer_url, where)
        logger.debug("%s: fetching %d IDs in batches of %d", layer_url, len(ids), batch_size)
        frames, reused = [], 0
        for chunk in batched(ids, batch_size):
            chunk = list(chunk)
            digest = hashlib.sha256(f"{where}|{oid_field}|{chunk}".encode()).hexdigest()[:16]
            got, was_saved = _staged(
                Path(staging) if staging else None,
                f"batch-{chunk[0]}-{chunk[-1]}-{digest}",
                lambda chunk=chunk: fetch_id_range(session, layer_url, where, oid_field, chunk),
            )
            frames.extend(got)
            reused += was_saved
        if reused:
            logger.info("%s: reused %d saved batches", layer_url, reused)
    elif supports_pagination(metadata):
        logger.debug("%s: no object ID field, paging by offset", layer_url)
        frames = fetch_by_offset(session, layer_url, where, batch_size)
    else:
        logger.warning("%s: no object ID field or pagination; using a single query", layer_url)
        frames = [query_features(session, layer_url, {"where": where})[0]]

    if not frames:
        # No matching features: one query still returns the CRS
        frames = [query_features(session, layer_url, {"where": where})[0]]

    return to_frame(frames)


def preview_features(
    session: requests.Session, layer_url: str, limit: int = 500
) -> gpd.GeoDataFrame:
    """A sample of features straight from the server, for datasets not yet harvested."""
    gdf, _ = query_features(session, layer_url, {"where": "1=1", "resultRecordCount": limit})
    return to_frame([gdf.head(limit)])
