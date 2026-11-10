from django.urls import path

from census import views

app_name = "census"

urlpatterns = [
    path("datasets/", views.dataset_list, name="dataset_list"),
    path("datasets/<int:pk>/", views.dataset_detail, name="dataset_detail"),
    path("datasets/<int:pk>/preview.geojson", views.dataset_preview, name="dataset_preview"),
    path("datasets/<int:pk>/watch/", views.dataset_watch, name="dataset_watch"),
    path("datasets/<int:pk>/harvest/", views.dataset_harvest, name="dataset_harvest"),
    path("places/", views.place_list, name="place_list"),
    path("places/counties.geojson", views.counties_geojson, name="counties_geojson"),
    path("places/<slug:slug>/", views.place_detail, name="place_detail"),
    path("coverage/", views.coverage, name="coverage"),
    path("sources/", views.source_list, name="source_list"),
    path("sources/add/", views.source_add, name="source_add"),
    path("sources/<slug:slug>/", views.source_detail, name="source_detail"),
    path("sources/<slug:slug>/crawl/", views.source_crawl, name="source_crawl"),
    path("changes/", views.changes, name="changes"),
]
