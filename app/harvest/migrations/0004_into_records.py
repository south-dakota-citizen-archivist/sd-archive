"""Features and harvests live in records now (copied by records 0002); drop the old tables."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("harvest", "0003_latest_only"),
        ("records", "0003_gis_layers_copy"),
    ]

    operations = [
        migrations.DeleteModel("Feature"),
        migrations.DeleteModel("Harvest"),
    ]
