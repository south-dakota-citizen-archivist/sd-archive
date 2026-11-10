"""Jurisdiction now lives in places (same table): repoint the foreign keys and drop the model
from census, without touching the database."""

import django.db.models.deletion
from django.db import migrations, models


def fk(related_name, **extra):
    return models.ForeignKey(
        blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
        related_name=related_name, to="places.jurisdiction", **extra,
    )  # fmt: skip


class Migration(migrations.Migration):
    dependencies = [
        ("census", "0009_remove_dataset_duplicate_of"),
        ("places", "0001_initial"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.AlterField("publisher", "jurisdiction", fk("publishers")),
                migrations.AlterField("source", "jurisdiction", fk(
                    "sources", help_text="Default jurisdiction for this source's datasets")),
                migrations.AlterField("dataset", "jurisdiction", fk("datasets")),
                migrations.DeleteModel("Jurisdiction"),
            ],
        ),
    ]  # fmt: skip
