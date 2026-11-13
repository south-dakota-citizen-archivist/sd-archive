"""GIS data: map features from county map apps, each with its shape and the details its
report page shows publicly (owners, summary, valuation, taxing units). Details an app keeps
behind an account aren't read."""

from records.sources._map_app.source import map_app_source

# app name → the county's fully qualified GEOID
APPS = {"LawrenceCountySD": "0500000US46081", "MinnehahaCountySD": "0500000US46099"}

SOURCE = map_app_source(
    "gis-data", APPS, name="GIS data", publisher="South Dakota counties",
    description="Map features from county GIS: each feature's shape, and the owners, valuation "
                "and other details its report page shows publicly.",
)  # fmt: skip
