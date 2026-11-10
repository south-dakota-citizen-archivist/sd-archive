"""places_jurisdiction: renamed from census_jurisdiction; more kinds (communities, school and
legislative districts); the fully qualified GEOID and OCD ID; dates in force and successors,
so former places (Shannon County) can stay."""

import django.db.models.deletion
from django.db import migrations, models


def fill_ids(apps, schema_editor):
    """Rows already loaded take their IDs from sdplaces. Rows sdplaces doesn't know (made by
    hand) get a fully qualified GEOID built from kind and GEOID."""
    import sdplaces

    prefix = {"state": "0400000US", "county": "0500000US", "place": "1600000US",
              "tribal": "2500000US"}  # fmt: skip
    Jurisdiction = apps.get_model("places", "Jurisdiction")
    for j in Jurisdiction.objects.all():
        try:
            p = sdplaces.get(j.geoid, j.kind)
            j.geoidfq, j.ocd_id = p.geoidfq, p.ocd_id
        except sdplaces.UnknownPlace:
            j.geoidfq = prefix.get(j.kind, f"{j.kind}:") + j.geoid
        j.save(update_fields=["geoidfq", "ocd_id"])


class Migration(migrations.Migration):
    dependencies = [
        ("places", "0001_initial"),
        ("census", "0010_jurisdiction_moves_to_places"),
    ]

    operations = [
        migrations.AlterModelTable("jurisdiction", None),
        migrations.AlterField(
            "jurisdiction", "kind",
            models.CharField(choices=[
                ("state", "State"), ("county", "County"), ("place", "City or town"),
                ("cdp", "Community (census designated place)"), ("tribal", "Tribal area"),
                ("school_district", "School district"), ("sldu", "State Senate district"),
                ("sldl", "State House district"),
            ], db_index=True, max_length=20),
        ),
        migrations.AddField("jurisdiction", "geoidfq", models.CharField(
            max_length=40, null=True,
            help_text="Fully qualified Census GEOID (0500000US46081), unique across kinds")),
        migrations.AddField("jurisdiction", "ocd_id", models.CharField(
            blank=True, default="", max_length=255, help_text="Open Civic Data division ID"),
            preserve_default=False),
        migrations.RunPython(fill_ids, migrations.RunPython.noop),
        migrations.AlterField("jurisdiction", "geoidfq", models.CharField(
            max_length=40, unique=True,
            help_text="Fully qualified Census GEOID (0500000US46081), unique across kinds")),
        migrations.AddField("jurisdiction", "valid_from",
                            models.DateField(blank=True, null=True)),
        migrations.AddField("jurisdiction", "valid_to", models.DateField(
            blank=True, null=True, help_text="Set for former places")),
        migrations.AddField("jurisdiction", "successor", models.ForeignKey(
            blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
            related_name="predecessors", to="places.jurisdiction",
            help_text="The place it became (Shannon County → Oglala Lakota County)")),
    ]  # fmt: skip
