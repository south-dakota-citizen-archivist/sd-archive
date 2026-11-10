from django.contrib.postgres.indexes import GinIndex
from django.db import models


class NameEntry(models.Model):
    """A person or organization name found in a harvested dataset.

    Rebuilt from harvest.Feature after each harvest: one row per (dataset, feature,
    field, normalized name), with the dates that name was valid, so former owners stay
    findable. The public.search_names view exposes these rows for cross-project search.
    """

    dataset = models.ForeignKey(
        "census.Dataset", on_delete=models.CASCADE, related_name="name_entries"
    )
    feature_key = models.CharField(max_length=100)
    field = models.CharField(max_length=100)
    name = models.TextField(help_text="As published (most recent spelling)")
    normalized = models.TextField(help_text="gis_hub_normalize_name(name)")
    valid_from = models.DateTimeField()
    valid_to = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            GinIndex(fields=["normalized"], opclasses=["gin_trgm_ops"], name="nameentry_trgm"),
            models.Index(fields=["dataset", "feature_key"]),
        ]

    def __str__(self) -> str:
        return self.name

    @property
    def is_current(self) -> bool:
        return self.valid_to is None
