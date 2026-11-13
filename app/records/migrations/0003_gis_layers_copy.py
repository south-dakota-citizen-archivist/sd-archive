"""Copy the GIS hub's harvested layers into records (a separate migration from the schema
change, so Postgres builds the new indexes before any rows arrive)."""

from django.db import migrations


def gis_layers_into_records(apps, schema_editor):
    """Every census dataset that was ever harvested gets a record set; its harvests become runs
    and its features records, with the same IDs (so harvest pages and export files stay)."""
    Dataset = apps.get_model("census", "Dataset")
    RecordSet = apps.get_model("records", "RecordSet")
    Harvest = apps.get_model("harvest", "Harvest")
    harvested = set(Harvest.objects.values_list("dataset_id", flat=True))
    for d in Dataset.objects.filter(pk__in=harvested).select_related("source"):
        RecordSet.objects.get_or_create(
            gis_dataset_id=d.pk,
            defaults={"slug": f"gis-{d.pk}", "name": d.name[:255], "dataset": "gis-data",
                      "publisher_id": d.source.publisher_id, "base_url": d.url[:1000],
                      "ingest": "gis"},
        )  # fmt: skip
    with schema_editor.connection.cursor() as cur:
        cur.execute(COPY_SQL)


COPY_SQL = """
INSERT INTO records_run (id, recordset_id, job_id, status, started_at, finished_at, forced,
    expected_count, source_edited_at, fetched_count, added, changed, removed, unchanged,
    content_hash, export, checks, error)
SELECT h.id, rs.id, h.job_id, h.status, h.started_at, h.finished_at, h.forced,
    h.expected_count, h.source_edited_at, h.fetched_count, h.added, h.changed, h.removed,
    h.unchanged, h.content_hash, h.export, h.checks, h.error
FROM harvest_harvest h JOIN records_recordset rs ON rs.gis_dataset_id = h.dataset_id;

INSERT INTO records_record (recordset_id, key, data, geometry, content_hash, source_url,
    first_seen, updated_at, run_id, legacy)
SELECT rs.id, f.key, f.properties, f.geometry, f.content_hash, '', f.first_seen, now(),
    f.harvest_id, '{}'::jsonb
FROM harvest_feature f JOIN records_recordset rs ON rs.gis_dataset_id = f.dataset_id;

SELECT setval(pg_get_serial_sequence('records_run', 'id'),
              greatest((SELECT max(id) FROM records_run), 1));
"""


class Migration(migrations.Migration):
    dependencies = [
        ("records", "0002_gis_layers"),
        ("harvest", "0003_latest_only"),
    ]

    operations = [migrations.RunPython(gis_layers_into_records, migrations.RunPython.noop)]
