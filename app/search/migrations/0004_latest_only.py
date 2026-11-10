"""Name entries are as of the latest harvest: no dates. search_names drops its date columns."""

import importlib

from django.db import migrations, models

previous = importlib.import_module("search.migrations.0003_view_names_places_jurisdiction")
OLD_VIEW = previous.VIEW
VIEW = """
DROP VIEW IF EXISTS search_names;
CREATE VIEW search_names AS
SELECT
    'sd-gis-hub'::text AS project,
    'gis_feature'::text AS record_type,
    d.name || ' (' || coalesce(j.name, s.name) || ')' AS record_label,
    n.name,
    n.normalized AS normalized_name,
    '/datasets/' || d.id || '/features/' || n.feature_key || '/' AS url,
    jsonb_build_object(
        'dataset_id', d.id,
        'dataset', d.name,
        'field', n.field,
        'jurisdiction', j.name,
        'source', s.name
    ) AS context
FROM search_nameentry n
JOIN census_dataset d ON d.id = n.dataset_id
JOIN census_source s ON s.id = d.source_id
LEFT JOIN places_jurisdiction j ON j.id = d.jurisdiction_id;
"""


class Migration(migrations.Migration):
    dependencies = [("search", "0003_view_names_places_jurisdiction")]

    operations = [
        migrations.RunSQL("DROP VIEW IF EXISTS search_names;", OLD_VIEW),
        migrations.RemoveField("nameentry", "valid_from"),
        migrations.RemoveField("nameentry", "valid_to"),
        migrations.AlterField(
            "nameentry",
            "name",
            models.TextField(help_text="As published"),
        ),
        migrations.RunSQL(VIEW, "DROP VIEW IF EXISTS search_names;"),
    ]
