from django.urls import path

from search import views

app_name = "search"

urlpatterns = [
    path("", views.name_search, name="name_search"),
]
