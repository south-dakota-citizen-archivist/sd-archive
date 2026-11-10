"""Load harvested features into GeoPandas, for notebooks and scripts.

from harvest.frames import load_dataset  # after django.setup(), e.g. in manage.py shell
parcels = load_dataset(641)                        # current records
parcels_then = load_dataset(641, as_of="2026-11-01")  # as they were then
"""

import json
from datetime import datetime

import geopandas as gpd
import pandas as pd
import shapely
from django.db import connection
from django.utils.dateparse import parse_datetime

SQL = """
SELECT key, properties, ST_AsBinary(geometry) AS wkb, valid_from, valid_to
FROM harvest_feature
WHERE dataset_id = %(dataset_id)s
  AND {when}
ORDER BY length(key), key
"""


def load_dataset(
    dataset_id: int, as_of: datetime | str | None = None, history: bool = False
) -> gpd.GeoDataFrame:
    """Current records (default), records as of a time, or every version (history=True).

    Columns are the dataset's attributes plus _key, _valid_from and _valid_to.
    """
    params: dict = {"dataset_id": dataset_id}
    if history:
        when = "TRUE"
    elif as_of is not None:
        if isinstance(as_of, str):
            as_of = parse_datetime(as_of) or datetime.fromisoformat(as_of)
        when = "valid_from <= %(as_of)s AND (valid_to IS NULL OR valid_to > %(as_of)s)"
        params["as_of"] = as_of
    else:
        when = "valid_to IS NULL"

    with connection.cursor() as cur:
        cur.execute(SQL.format(when=when), params)
        rows = cur.fetchall()

    # Raw cursors return jsonb as text (Django decodes it only for model fields)
    attrs = pd.DataFrame.from_records(
        [json.loads(r[1]) if isinstance(r[1], str) else r[1] for r in rows]
    )
    attrs["_key"] = [r[0] for r in rows]
    attrs["_valid_from"] = [r[3] for r in rows]
    attrs["_valid_to"] = [r[4] for r in rows]
    # jsonb stores keys alphabetically; put columns back in the dataset's field order
    from census.models import Dataset

    order = Dataset.objects.get(pk=dataset_id).field_names
    attrs = attrs[[c for c in order if c in attrs] + [c for c in attrs if c not in order]]
    geometry = shapely.from_wkb([bytes(r[2]) if r[2] is not None else None for r in rows])
    return gpd.GeoDataFrame(attrs, geometry=geometry, crs=4326)
