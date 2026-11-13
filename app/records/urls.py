from django.urls import path

from records import views

app_name = "records"
urlpatterns = [
    path("", views.recordset_list, name="recordset_list"),
    path("runs/<int:pk>/", views.run_detail, name="run_detail"),
    path("documents/<int:pk>/file/", views.document_file, name="document_file"),
    path("<slug:slug>/", views.recordset_detail, name="recordset_detail"),
    path("<slug:slug>/table/", views.recordset_table, name="recordset_table"),
    path("<slug:slug>/records.csv", views.recordset_csv, name="recordset_csv"),
    path("<slug:slug>/r/<path:key>", views.record_detail, name="record_detail"),
]
