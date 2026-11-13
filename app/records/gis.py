"""GIS layers as record sets: a watched layer's features are Records, its harvests Runs."""

from records.models import RecordSet

# The catalog dataset GIS layers belong to (catalog/datasets.toml)
CATALOG_DATASET = "gis-data"


def recordset_for(dataset) -> RecordSet:
    """The record set holding a census Dataset's features, made the first time it's needed."""
    try:
        return dataset.recordset
    except RecordSet.DoesNotExist:
        pass
    rs, _ = RecordSet.objects.get_or_create(
        gis_dataset=dataset,
        defaults={
            "slug": f"gis-{dataset.pk}",
            "name": dataset.name[:255],
            "dataset": CATALOG_DATASET,
            "publisher": dataset.source.publisher,
            "base_url": dataset.url[:1000],
            "ingest": RecordSet.Ingest.GIS,
        },
    )
    return rs
