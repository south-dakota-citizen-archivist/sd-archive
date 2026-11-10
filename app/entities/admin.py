from django.contrib import admin
from unfold.admin import ModelAdmin

from entities.models import Mention


@admin.register(Mention)
class MentionAdmin(ModelAdmin):
    list_display = ["name", "schema", "dataset", "record_key", "role", "gold_entity_id"]
    list_filter = ["schema", "dataset", "role"]
    search_fields = ["name", "normalized", "record_key", "gold_entity_id", "entity_id"]
    readonly_fields = ["created_at", "updated_at"]
    raw_id_fields = ["jurisdiction"]
