"""Recreate search_names naming places_jurisdiction. (Postgres had already followed the
rename; this keeps the SQL readable.)"""

import importlib

from django.db import migrations

previous = importlib.import_module("search.migrations.0002_normalize_function_and_view")
VIEW = previous.SEARCH_NAMES_VIEW.replace("census_jurisdiction", "places_jurisdiction")


class Migration(migrations.Migration):
    dependencies = [
        ("search", "0002_normalize_function_and_view"),
        ("places", "0002_more_kinds_ids_and_dates"),
    ]

    operations = [migrations.RunSQL(VIEW, previous.SEARCH_NAMES_VIEW)]
