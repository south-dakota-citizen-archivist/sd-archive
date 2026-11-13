"""Rows registered as the old platform value become county map apps: the platform, and the
names, slugs and notes written from it."""

from django.db import migrations

OLD_SUFFIX = (" (" + "Bea" + "con)", "-" + "bea" + "con")  # spelled apart: the old name


def forward(apps, schema_editor):
    Source = apps.get_model("census", "Source")
    Candidate = apps.get_model("census", "SourceCandidate")
    for s in Source.objects.filter(platform="beacon"):
        s.platform = "map_app"
        if s.name.endswith(OLD_SUFFIX[0]):
            s.name = s.name[: -len(OLD_SUFFIX[0])] + " (map app)"
        if s.slug.endswith(OLD_SUFFIX[1]):
            s.slug = s.slug[: -len(OLD_SUFFIX[1])] + "-map-app"
        s.found_via = "map app list (seeds/map-apps.html)"
        s.notes = ("A county map app: the county's GIS data and property records on a hosted "
                   "site. The hub links to it; its features are read by the gis-data record "
                   "set, not crawled here.")  # fmt: skip
        s.save()
    Candidate.objects.filter(platform="beacon").update(platform="map_app")


class Migration(migrations.Migration):
    dependencies = [("census", "0011_publisher_parent")]
    operations = [migrations.RunPython(forward, migrations.RunPython.noop)]
