from django.urls import path

from harvest import views

app_name = "harvest"

urlpatterns = [
    path("gis/datasets/<int:pk>/table/", views.dataset_table, name="dataset_table"),
    path(
        "gis/datasets/<int:pk>/table/preview/",
        views.dataset_preview_table,
        name="dataset_preview_table",
    ),
    path("gis/datasets/<int:pk>/map/", views.dataset_map, name="dataset_map"),
    path("gis/datasets/<int:pk>/changes/", views.dataset_changes, name="dataset_changes"),
    path("gis/datasets/<int:pk>/download/", views.dataset_download, name="dataset_download"),
    path("gis/datasets/<int:pk>/download.<str:fmt>", views.download, name="download"),
    path("gis/datasets/<int:pk>/features/<str:key>/", views.feature_detail, name="feature_detail"),
    path("gis/datasets/<int:pk>/features/<str:key>.json", views.feature_json, name="feature_json"),
    path("gis/harvests/<int:pk>/", views.harvest_detail, name="harvest_detail"),
    path("gis/tiles/<int:pk>/<int:z>/<int:x>/<int:y>.pbf", views.tile, name="tile"),
]
