from django.db import migrations

# One normalization rule shared by every project that publishes a search_names view:
# uppercase, accents removed, punctuation to spaces, whitespace collapsed.
# unaccent() isn't IMMUTABLE on its own, so it's called with an explicit dictionary
# inside a function declared IMMUTABLE, which lets indexes use it.
NORMALIZE_FUNCTION = r"""
CREATE OR REPLACE FUNCTION gis_hub_normalize_name(value text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT AS $$
    SELECT btrim(regexp_replace(
        regexp_replace(upper(public.unaccent('public.unaccent'::regdictionary, value)),
                       '[^[:alnum:]]+', ' ', 'g'),
        '\s+', ' ', 'g'))
$$;
"""

# The shared format for name search across databases. url is relative to this app's base URL.
SEARCH_NAMES_VIEW = """
CREATE OR REPLACE VIEW search_names AS
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
    ) AS context,
    n.valid_from,
    n.valid_to
FROM search_nameentry n
JOIN census_dataset d ON d.id = n.dataset_id
JOIN census_source s ON s.id = d.source_id
LEFT JOIN census_jurisdiction j ON j.id = d.jurisdiction_id;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("search", "0001_initial"),
        ("census", "0001_initial"),
    ]
    # The view's SQL names census_jurisdiction, which places 0002 renames (the view follows)
    run_before = [("places", "0002_more_kinds_ids_and_dates")]

    operations = [
        migrations.RunSQL(
            NORMALIZE_FUNCTION, "DROP FUNCTION IF EXISTS gis_hub_normalize_name(text);"
        ),
        migrations.RunSQL(SEARCH_NAMES_VIEW, "DROP VIEW IF EXISTS search_names;"),
    ]
