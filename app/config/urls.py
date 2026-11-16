from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path, re_path
from django.views.generic import RedirectView

from config.api import api
from core import views as core_views

urlpatterns = [
    path("", core_views.home, name="home"),
    path("jobs/", core_views.job_list, name="job_list"),
    path("jobs/<int:pk>/", core_views.job_detail, name="job_detail"),
    path("", include("census.urls")),
    path("", include("harvest.urls")),
    path("records/", include("records.urls")),
    path("search/", include("search.urls")),
    path("api/v1/", api.urls),
    path("admin/", admin.site.urls),
    # the GIS pages' addresses before they moved under gis/
    re_path(
        r"^(?P<rest>(?:datasets|sources|coverage|changes|harvests)/.*)$",
        RedirectView.as_view(url="/gis/%(rest)s", query_string=True),
    ),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
