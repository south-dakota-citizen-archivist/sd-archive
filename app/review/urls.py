from django.urls import path

from review import views

app_name = "review"
urlpatterns = [
    path("", views.index, name="index"),
    path("page.png", views.page_image, name="page_image"),
    path("<slug:source>/<str:key>/", views.detail, name="detail"),
    path("<slug:source>/<str:key>/decide/", views.decide, name="decide"),
    path("<slug:source>/<str:key>/undo/", views.undo, name="undo"),
    path("<slug:source>/<str:key>/check/", views.check, name="check"),
]
