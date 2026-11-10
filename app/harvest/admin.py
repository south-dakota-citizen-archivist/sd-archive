from django.contrib import admin
from unfold.admin import ModelAdmin

from harvest.models import Feature, Harvest


@admin.register(Harvest)
class HarvestAdmin(ModelAdmin):
    list_display = [
        "dataset",
        "status",
        "started_at",
        "fetched_count",
        "added",
        "changed",
        "removed",
        "forced",
    ]
    list_filter = ["status"]
    raw_id_fields = ["dataset", "job"]
    search_fields = ["dataset__name"]


@admin.register(Feature)
class FeatureAdmin(ModelAdmin):
    list_display = ["key", "dataset", "first_seen", "harvest"]
    raw_id_fields = ["dataset", "harvest"]
    search_fields = ["key"]
    exclude = ["geometry"]

    def has_add_permission(self, request):
        return False
