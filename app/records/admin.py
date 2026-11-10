from django.contrib import admin
from unfold.admin import ModelAdmin

from records.models import Document, Extraction, Record, RecordSet, Run


@admin.register(RecordSet)
class RecordSetAdmin(ModelAdmin):
    list_display = ["name", "slug", "dataset", "ingest", "publisher", "active"]
    list_filter = ["ingest", "dataset", "active"]
    search_fields = ["name", "slug"]
    raw_id_fields = ["publisher", "gis_dataset"]


@admin.register(Run)
class RunAdmin(ModelAdmin):
    list_display = ["recordset", "status", "started_at", "fetched_count", "added", "changed",
                    "removed", "forced"]  # fmt: skip
    list_filter = ["status", "recordset__ingest"]
    raw_id_fields = ["recordset", "job"]
    search_fields = ["recordset__name"]


@admin.register(Record)
class RecordAdmin(ModelAdmin):
    list_display = ["key", "recordset", "first_seen", "updated_at"]
    list_filter = ["recordset__ingest"]
    raw_id_fields = ["recordset", "run", "jurisdiction"]
    search_fields = ["key"]
    exclude = ["geometry"]

    def has_add_permission(self, request):
        return False


@admin.register(Document)
class DocumentAdmin(ModelAdmin):
    list_display = ["__str__", "kind", "category", "status", "pages"]
    list_filter = ["kind", "status", "category"]
    raw_id_fields = ["record"]
    search_fields = ["filename", "url", "sha256"]
    exclude = ["text"]


@admin.register(Extraction)
class ExtractionAdmin(ModelAdmin):
    list_display = ["__str__", "model", "review", "reviewed_at"]
    list_filter = ["review", "schema"]
    raw_id_fields = ["document", "reviewed_by"]
