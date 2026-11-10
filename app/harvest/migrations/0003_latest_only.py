"""Features keep only their latest version: closed versions go, each feature keeps when it
was first seen and the harvest that last wrote it, and only each dataset's latest export
file is kept."""

from pathlib import Path

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.db.models import Min


def keep_latest(apps, schema_editor):
    Feature = apps.get_model("harvest", "Feature")
    first = {
        (r["dataset_id"], r["key"]): r["first"]
        for r in Feature.objects.values("dataset_id", "key").annotate(first=Min("valid_from"))
    }
    Feature.objects.exclude(valid_to=None).delete()
    for f in Feature.objects.filter(first_seen=None).only("pk", "dataset_id", "key"):
        f.first_seen = first.get((f.dataset_id, f.key))
        f.save(update_fields=["first_seen"])

    Harvest = apps.get_model("harvest", "Harvest")
    media = Path(settings.MEDIA_ROOT)
    seen = set()
    for h in Harvest.objects.exclude(export="").order_by("dataset_id", "-started_at"):
        path = media / h.export.name
        if h.dataset_id in seen:
            path.unlink(missing_ok=True)
            Harvest.objects.filter(pk=h.pk).update(export="")
            continue
        seen.add(h.dataset_id)
        latest = f"exports/{h.dataset_id}/latest.parquet"
        if path.exists():
            path.replace(media / latest)
        Harvest.objects.filter(pk=h.pk).update(export=latest)


class Migration(migrations.Migration):
    dependencies = [("harvest", "0002_harvest_skipped")]

    operations = [
        migrations.AddField("feature", "first_seen", models.DateTimeField(null=True)),
        migrations.RunPython(keep_latest, migrations.RunPython.noop),
        migrations.RemoveIndex("feature", "harvest_fea_dataset_9d96c5_idx"),
        migrations.RemoveIndex("feature", "feature_current"),
        migrations.RemoveField("feature", "closed_by"),
        migrations.RemoveField("feature", "valid_from"),
        migrations.RemoveField("feature", "valid_to"),
        migrations.RenameField("feature", "created_by", "harvest"),
        migrations.AlterField("feature", "harvest", models.ForeignKey(
            blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
            related_name="features", to="harvest.harvest",
            help_text="The harvest that last wrote it")),
        migrations.AlterField("feature", "first_seen", models.DateTimeField(
            help_text="When a harvest first found it")),
        migrations.AlterModelOptions("feature", {"ordering": ["dataset", "key"]}),
        migrations.AddConstraint("feature", models.UniqueConstraint(
            fields=("dataset", "key"), name="feature_key")),
        migrations.AlterField("harvest", "export", models.FileField(
            blank=True, upload_to="exports/",
            help_text="Set on the harvest that wrote the latest file")),
    ]  # fmt: skip
