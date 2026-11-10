"""Jurisdiction moves here from census. This takes over the model as it was, on the existing
table, without touching the database; 0002 renames the table and adds fields."""

import django.contrib.gis.db.models.fields
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [("census", "0009_remove_dataset_duplicate_of")]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.CreateModel(
                    name="Jurisdiction",
                    fields=[
                        ("id", models.BigAutoField(auto_created=True, primary_key=True,
                                                   serialize=False, verbose_name="ID")),
                        ("kind", models.CharField(
                            choices=[("state", "State"), ("county", "County"),
                                     ("place", "City or town"), ("tribal", "Tribal area")],
                            db_index=True, max_length=20)),
                        ("geoid", models.CharField(help_text="Census GEOID", max_length=20)),
                        ("name", models.CharField(help_text='Full name, e.g. "Lawrence County"',
                                                  max_length=255)),
                        ("short_name", models.CharField(help_text='e.g. "Lawrence"',
                                                        max_length=255)),
                        ("slug", models.SlugField(max_length=255, unique=True)),
                        ("boundary", django.contrib.gis.db.models.fields.MultiPolygonField(
                            blank=True, null=True, srid=4326)),
                        ("parent", models.ForeignKey(
                            blank=True, null=True,
                            on_delete=django.db.models.deletion.SET_NULL,
                            related_name="children", to="places.jurisdiction")),
                    ],
                    options={
                        "ordering": ["kind", "name"],
                        "db_table": "census_jurisdiction",
                        "constraints": [models.UniqueConstraint(
                            fields=("kind", "geoid"), name="unique_jurisdiction_geoid")],
                    },
                ),
            ],
        ),
    ]  # fmt: skip
