from django.urls import path

from census import views

app_name = "census"

urlpatterns = [
    path("gis/datasets/", views.dataset_list, name="dataset_list"),
    path("gis/datasets/<int:pk>/", views.dataset_detail, name="dataset_detail"),
    path("gis/datasets/<int:pk>/preview.geojson", views.dataset_preview, name="dataset_preview"),
    path("gis/datasets/<int:pk>/watch/", views.dataset_watch, name="dataset_watch"),
    path("gis/datasets/<int:pk>/harvest/", views.dataset_harvest, name="dataset_harvest"),
    path("places/", views.place_list, name="place_list"),
    path("places/counties.geojson", views.counties_geojson, name="counties_geojson"),
    path("places/<slug:slug>/", views.place_detail, name="place_detail"),
    path("gis/coverage/", views.coverage, name="coverage"),
    path("gis/sources/", views.source_list, name="source_list"),
    path("gis/sources/add/", views.source_add, name="source_add"),
    path("gis/sources/<slug:slug>/", views.source_detail, name="source_detail"),
    path("gis/sources/<slug:slug>/crawl/", views.source_crawl, name="source_crawl"),
    path("gis/changes/", views.changes, name="changes"),
    path("gis/candidates/", views.candidate_list, name="candidate_list"),
    path("gis/candidates/find/", views.candidate_find, name="candidate_find"),
    path("gis/candidates/<int:pk>/review/", views.candidate_review, name="candidate_review"),
]
