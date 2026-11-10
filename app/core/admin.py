from django.contrib import admin
from unfold.admin import ModelAdmin

from core.models import Job


@admin.register(Job)
class JobAdmin(ModelAdmin):
    list_display = ["kind", "label", "status", "created_at", "started_at", "finished_at"]
    list_filter = ["kind", "status"]
    search_fields = ["label", "error"]
    readonly_fields = [f.name for f in Job._meta.fields]
