from django.contrib import admin
from unfold.admin import ModelAdmin

from places.models import Jurisdiction


@admin.register(Jurisdiction)
class JurisdictionAdmin(ModelAdmin):
    list_display = ["name", "kind", "geoid", "parent", "valid_to"]
    list_filter = ["kind"]
    search_fields = ["name", "geoid", "slug", "geoidfq"]
    exclude = ["boundary"]
    readonly_fields = ["kind", "geoid", "geoidfq", "ocd_id", "valid_from", "valid_to"]
