"""The `published` schema: views that define everything that can be exported, and the
sd_export role, which can read only them. (Not "public", which is Postgres's default schema,
where every table lives.) A view reads its tables with its owner's rights, so sd_export sees
exactly the columns and rows a view chooses and none of the tables behind it."""

from django.db import migrations

SQL = r"""
CREATE SCHEMA IF NOT EXISTS published;
COMMENT ON SCHEMA published IS
  'Everything that can be exported: views over the archive. The sd_export role reads only this.';

DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'sd_export') THEN
    CREATE ROLE sd_export NOLOGIN;
  END IF;
END $$;
GRANT USAGE ON SCHEMA published TO sd_export;
ALTER DEFAULT PRIVILEGES IN SCHEMA published GRANT SELECT ON TABLES TO sd_export;

-- Every place, with the IDs other data joins on; former places with what they became
CREATE OR REPLACE VIEW published.places AS
SELECT j.geoidfq, j.kind, j.geoid, j.name, j.short_name, j.slug, j.ocd_id,
       parent.geoidfq AS parent_geoidfq,
       j.valid_from, j.valid_to,
       successor.geoidfq AS successor_geoidfq,
       j.boundary
FROM places_jurisdiction j
LEFT JOIN places_jurisdiction parent ON parent.id = j.parent_id
LEFT JOIN places_jurisdiction successor ON successor.id = j.successor_id;

-- The census of public GIS data: every layer and table found on a government's server
CREATE OR REPLACE VIEW published.gis_datasets AS
SELECT d.id, d.name, d.url, d.kind, d.geometry_type, d.description, d.feature_count,
       d.status = 'watched' AS archived,
       s.name AS source, s.url AS source_url, p.name AS publisher,
       j.geoidfq AS place_geoidfq, j.name AS place,
       d.first_seen, d.last_seen, d.source_edited_at, d.last_harvested_at
FROM census_dataset d
JOIN census_source s ON s.id = d.source_id
LEFT JOIN census_publisher p ON p.id = s.publisher_id
LEFT JOIN places_jurisdiction j ON j.id = d.jurisdiction_id
WHERE d.removed_at IS NULL;

GRANT SELECT ON ALL TABLES IN SCHEMA published TO sd_export;
"""

REVERSE = "DROP SCHEMA IF EXISTS published CASCADE;"


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0002_job_worker_stalled"),
        ("places", "0002_more_kinds_ids_and_dates"),
        ("census", "0010_jurisdiction_moves_to_places"),
    ]

    operations = [migrations.RunSQL(SQL, REVERSE)]
