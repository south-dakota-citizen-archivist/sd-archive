from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("census", "0008_drop_copies")]

    operations = [migrations.RemoveField(model_name="dataset", name="duplicate_of")]
