"""GIS pages moved under /gis/: search_names links to a feature there."""

import importlib

from django.db import migrations

previous = importlib.import_module("search.migrations.0004_latest_only")
VIEW = previous.VIEW.replace("'/datasets/' ||", "'/gis/datasets/' ||")
assert VIEW != previous.VIEW


class Migration(migrations.Migration):
    dependencies = [("search", "0004_latest_only")]

    operations = [migrations.RunSQL(VIEW, previous.VIEW)]
