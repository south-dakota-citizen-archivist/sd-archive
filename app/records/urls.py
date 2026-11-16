from django.urls import path

from records import views
from records.sources.legislature_blue_book_bios import review as bio_review

app_name = "records"
urlpatterns = [
    path("", views.recordset_list, name="recordset_list"),
    path("runs/<int:pk>/", views.run_detail, name="run_detail"),
    path("documents/<int:pk>/file/", views.document_file, name="document_file"),
    path("legislature-blue-book-bios/review/", bio_review.page, name="bio_review"),
    path("legislature-blue-book-bios/review/<int:pk>/", bio_review.decide, name="bio_decide"),
    path(
        "legislature-blue-book-bios/images/<str:image_id>.webp", bio_review.image, name="bio_image"
    ),
    path("<slug:slug>/", views.recordset_detail, name="recordset_detail"),
    path("<slug:slug>/table/", views.recordset_table, name="recordset_table"),
    path("<slug:slug>/records.csv", views.recordset_csv, name="recordset_csv"),
    path("<slug:slug>/<int:pk>/", views.record_detail, name="record_detail"),
]
