from django.contrib import admin
from unfold.admin import ModelAdmin

from search.models import NameEntry


@admin.register(NameEntry)
class NameEntryAdmin(ModelAdmin):
    list_display = ["name", "field", "dataset", "feature_key"]
    search_fields = ["normalized"]
    raw_id_fields = ["dataset"]

    def has_add_permission(self, request):
        return False
