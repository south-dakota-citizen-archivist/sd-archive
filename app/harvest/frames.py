"""Load harvested features into GeoPandas, for notebooks and scripts.

from harvest.frames import load_dataset  # after django.setup(), e.g. in manage.py shell
parcels = load_dataset(641)
"""

import json

import geopandas as gpd
import pandas as pd
import shapely
from django.db import connection

SQL = """
SELECT f.key, f.data, ST_AsBinary(f.geometry) AS wkb
FROM records_record f JOIN records_recordset rs ON rs.id = f.recordset_id
WHERE rs.gis_dataset_id = %(dataset_id)s AND f.dropped_at IS NULL
ORDER BY length(f.key), f.key
"""


def load_dataset(dataset_id: int) -> gpd.GeoDataFrame:
    """A dataset's records as of its latest harvest. Columns are its attributes plus _key."""
    with connection.cursor() as cur:
        cur.execute(SQL, {"dataset_id": dataset_id})
        rows = cur.fetchall()

    # Raw cursors return jsonb as text (Django decodes it only for model fields)
    attrs = pd.DataFrame.from_records(
        [json.loads(r[1]) if isinstance(r[1], str) else r[1] for r in rows]
    )
    attrs["_key"] = [r[0] for r in rows]
    # jsonb stores keys alphabetically; put columns back in the dataset's field order
    from census.models import Dataset

    order = Dataset.objects.get(pk=dataset_id).field_names
    attrs = attrs[[c for c in order if c in attrs] + [c for c in attrs if c not in order]]
    geometry = shapely.from_wkb([bytes(r[2]) if r[2] is not None else None for r in rows])
    return gpd.GeoDataFrame(attrs, geometry=geometry, crs=4326)
