"""Build and query the name index (search.NameEntry)."""

from django.db import connection, transaction

from census.models import Dataset

# One row per (dataset, feature, field, normalized name), as of the latest harvest
REBUILD_SQL = """
INSERT INTO search_nameentry (dataset_id, feature_key, field, name, normalized)
SELECT DISTINCT ON (f.key, fld, gis_hub_normalize_name(f.data ->> fld))
    rs.gis_dataset_id,
    f.key,
    fld,
    btrim(f.data ->> fld),
    gis_hub_normalize_name(f.data ->> fld)
FROM records_record f JOIN records_recordset rs ON rs.id = f.recordset_id
CROSS JOIN unnest(%(fields)s::text[]) AS fld
WHERE rs.gis_dataset_id = %(dataset_id)s
  AND gis_hub_normalize_name(f.data ->> fld) NOT IN ('', '0', 'NONE', 'NULL')
"""

# Two steps, so one owner with hundreds of parcels can't crowd every other name out:
# first rank distinct names, then fetch the records for the best ones.
NAMES_SQL = r"""
WITH q AS (
    SELECT gis_hub_normalize_name(%(q)s) AS norm,
           regexp_split_to_array(gis_hub_normalize_name(%(q)s), ' ') AS words
),
hits AS (
    SELECT
        n.normalized,
        min(n.name) AS name,
        count(*) AS n_records,
        count(DISTINCT n.dataset_id) AS n_datasets,
        max(word_similarity(q.norm, n.normalized)) AS score
    FROM search_nameentry n
    CROSS JOIN q
    WHERE (q.norm <%% n.normalized OR n.normalized LIKE '%%' || q.norm || '%%')
    GROUP BY n.normalized
)
SELECT h.*
FROM hits h, q
ORDER BY
    -- exact phrase, then every query word starting a word in the name, then fuzzy
    (h.normalized LIKE '%%' || q.norm || '%%') DESC,
    (SELECT bool_and(h.normalized ~ ('\m' || w)) FROM unnest(q.words) w) DESC,
    h.score DESC, h.n_records DESC, h.normalized
LIMIT %(limit)s
"""

RECORDS_SQL = """
SELECT
    n.normalized, n.name, n.field, n.feature_key,
    d.id AS dataset_id, d.name AS dataset_name,
    j.name AS jurisdiction_name, j.slug AS jurisdiction_slug
FROM search_nameentry n
JOIN census_dataset d ON d.id = n.dataset_id
LEFT JOIN places_jurisdiction j ON j.id = d.jurisdiction_id
WHERE n.normalized = ANY(%(names)s)
ORDER BY n.normalized, d.name, n.feature_key
"""


@transaction.atomic
def rebuild(dataset: Dataset) -> int:
    with connection.cursor() as cur:
        cur.execute("DELETE FROM search_nameentry WHERE dataset_id = %s", [dataset.pk])
        if not dataset.name_fields:
            return 0
        cur.execute(REBUILD_SQL, {"dataset_id": dataset.pk, "fields": list(dataset.name_fields)})
        return cur.rowcount


def fetch(cur, sql: str, params: dict) -> list[dict]:
    cur.execute(sql, params)
    cols = [c.name for c in cur.description]
    return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]


def search(q: str, limit: int = 50, records_per_name: int = 25) -> list[dict]:
    """Best-matching names, each with up to records_per_name of the records it appears on."""
    q = (q or "").strip()
    if len(q) < 3:
        return []
    with connection.cursor() as cur:
        names = fetch(cur, NAMES_SQL, {"q": q, "limit": limit})
        if not names:
            return []
        records = fetch(
            cur,
            RECORDS_SQL,
            {"names": [n["normalized"] for n in names]},
        )
    by_name: dict[str, list] = {}
    for r in records:
        bucket = by_name.setdefault(r["normalized"], [])
        if len(bucket) < records_per_name:
            r["url"] = f"/datasets/{r['dataset_id']}/features/{r['feature_key']}/"
            bucket.append(r)
    for n in names:
        n["records"] = by_name.get(n["normalized"], [])
    return names
