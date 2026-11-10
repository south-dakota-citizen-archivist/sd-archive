"""Copies of other layers are no longer kept: delete the ones already flagged. The flag goes
in the next migration (Postgres won't alter a table with deletes pending in the same
transaction)."""

from django.db import migrations


def delete_copies(apps, schema_editor):
    Dataset = apps.get_model("census", "Dataset")
    Dataset.objects.exclude(duplicate_of=None).exclude(status="watched").filter(
        harvests__isnull=True
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("census", "0007_dataset_source_edited_at"),
        ("harvest", "0002_harvest_skipped"),
    ]

    operations = [
        migrations.RunPython(delete_copies, migrations.RunPython.noop),
    ]
