from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False
    dependencies = [("records", "0008_gis_data")]
    operations = [
        AddIndexConcurrently(
            model_name="record",
            index=models.Index(
                condition=models.Q(dropped_at=None),
                fields=["recordset"],
                name="record_current_by_set",
            ),
        ),
    ]
