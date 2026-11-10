from django.contrib import admin, messages
from unfold.admin import ModelAdmin, TabularInline

from census import seed
from census.models import (
    Crawl,
    Dataset,
    DatasetTopic,
    Publisher,
    Service,
    Source,
    SourceCandidate,
    Topic,
)
from core.tasks import start_crawl, start_harvest


@admin.register(Publisher)
class PublisherAdmin(ModelAdmin):
    list_display = ["name", "jurisdiction", "website"]
    search_fields = ["name"]
    autocomplete_fields = ["jurisdiction"]


@admin.register(Source)
class SourceAdmin(ModelAdmin):
    list_display = ["name", "platform", "publisher", "jurisdiction", "active", "last_crawled_at"]
    list_filter = ["platform", "active"]
    search_fields = ["name", "url"]
    autocomplete_fields = ["publisher", "jurisdiction"]
    readonly_fields = ["last_crawled_at", "last_scheduled_at", "created_at"]
    actions = ["crawl_now"]

    @admin.action(description="Crawl now")
    def crawl_now(self, request, queryset):
        crawlable = [s for s in queryset if s.crawlable]
        for source in crawlable:
            start_crawl(source)
        messages.success(request, f"Queued {len(crawlable)} crawl(s).")
        if skipped := len(queryset) - len(crawlable):
            messages.warning(request, f"Skipped {skipped} source(s) on platforms with no crawler.")


@admin.register(Service)
class ServiceAdmin(ModelAdmin):
    list_display = ["name", "type", "source", "error", "last_seen", "removed_at"]
    list_filter = ["source", "type"]
    search_fields = ["name", "url"]


@admin.register(Topic)
class TopicAdmin(ModelAdmin):
    list_display = ["name", "slug", "name_pattern", "field_pattern", "order"]
    list_editable = ["order"]


class DatasetTopicInline(TabularInline):
    model = DatasetTopic
    extra = 0


@admin.register(Dataset)
class DatasetAdmin(ModelAdmin):
    list_display = [
        "name",
        "source",
        "jurisdiction",
        "kind",
        "feature_count",
        "status",
        "last_harvested_at",
        "removed_at",
    ]
    list_filter = ["status", "kind", "source", "topics", ("removed_at", admin.EmptyFieldListFilter)]
    search_fields = ["name", "url", "search_text"]
    autocomplete_fields = ["jurisdiction"]
    raw_id_fields = ["service"]
    inlines = [DatasetTopicInline]
    readonly_fields = [
        "source",
        "url",
        "fields",
        "fields_hash",
        "first_seen",
        "last_seen",
        "removed_at",
        "last_harvested_at",
        "last_scheduled_at",
        "search_text",
    ]
    actions = ["watch", "unwatch", "harvest_now"]

    @admin.action(description="Watch (harvest on a schedule)")
    def watch(self, request, queryset):
        n = queryset.update(status=Dataset.Status.WATCHED)
        seed.schedule_dump()  # update() skips the save signals
        messages.success(request, f"Watching {n} dataset(s).")

    @admin.action(description="Stop watching")
    def unwatch(self, request, queryset):
        n = queryset.update(status=Dataset.Status.DISCOVERED)
        seed.schedule_dump()
        messages.success(request, f"Stopped watching {n} dataset(s).")

    @admin.action(description="Harvest now")
    def harvest_now(self, request, queryset):
        for dataset in queryset:
            start_harvest(dataset)
        messages.success(request, f"Queued {queryset.count()} harvest(s).")


@admin.register(Crawl)
class CrawlAdmin(ModelAdmin):
    list_display = [
        "source",
        "status",
        "started_at",
        "datasets_seen",
        "datasets_added",
        "datasets_removed",
        "service_errors",
    ]
    list_filter = ["status", "source"]


@admin.register(SourceCandidate)
class SourceCandidateAdmin(ModelAdmin):
    list_display = ["url", "title", "platform", "found_via", "status", "created_at"]
    list_filter = ["status", "found_via"]
    search_fields = ["url", "title"]
