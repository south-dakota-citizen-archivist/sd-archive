"""Settings for a single-user local app. Override anything via env vars."""

import importlib.util
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


SECRET_KEY = env("DJANGO_SECRET_KEY", "local-only-not-secret")
DEBUG = env("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "[::1]"]


def bundled_library(package: str, pattern: str) -> str:
    """A shared library shipped inside a wheel (macOS: pkg/.dylibs, Linux: pkg.libs)."""
    spec = importlib.util.find_spec(package)
    if not spec or not spec.origin:
        return ""
    pkg_dir = Path(spec.origin).parent
    for libs in (pkg_dir / ".dylibs", pkg_dir.parent / f"{package}.libs"):
        if matches := sorted(libs.glob(pattern)):
            return str(matches[0])
    return ""


# GeoDjango uses the GDAL and GEOS that pyogrio and shapely already bundle, so only one
# copy of each is loaded. Homebrew's GDAL links its own Apache Arrow, which collides with
# pyarrow's ("Attempted to register factory for scheme 'file'") when writing GeoParquet.
GDAL_LIBRARY_PATH = env("GDAL_LIBRARY_PATH") or bundled_library("pyogrio", "libgdal*")
GEOS_LIBRARY_PATH = env("GEOS_LIBRARY_PATH") or bundled_library("shapely", "libgeos_c*")
_pyogrio_dir = Path(importlib.util.find_spec("pyogrio").origin).parent
for var, sub in (("PROJ_DATA", "proj_data"), ("GDAL_DATA", "gdal_data")):
    if (_pyogrio_dir / sub).is_dir():
        os.environ.setdefault(var, str(_pyogrio_dir / sub))

INSTALLED_APPS = [
    "unfold",
    "unfold.contrib.filters",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "django.contrib.gis",
    "django.contrib.postgres",
    "core",
    "census",
    "harvest",
    "search",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# Local Postgres with PostGIS, configured by the usual PG* environment variables.
DATABASES = {
    "default": {
        "ENGINE": "django.contrib.gis.db.backends.postgis",
        "NAME": env("PGDATABASE", "sd_gis_hub"),
        "USER": env("PGUSER", os.environ.get("USER", "")),
        "PASSWORD": env("PGPASSWORD"),
        "HOST": env("PGHOST", "localhost"),
        "PORT": env("PGPORT", "5432"),
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LANGUAGE_CODE = "en-us"
TIME_ZONE = "America/Chicago"
USE_I18N = False
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [
    BASE_DIR / "static",
    # Front-end libraries come straight from node_modules; no copy step
    ("vendor/htmx", BASE_DIR / "node_modules/htmx.org/dist"),
    ("vendor/maplibre", BASE_DIR / "node_modules/maplibre-gl/dist"),
]
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

# Local status in place of hosted monitoring (`just status`)
# macOS notifications when work fails (core.notify); SD_NOTIFY=0 turns them off
NOTIFY = env("SD_NOTIFY", "1") == "1"
# Big downloads refuse to start, and `just status` warns, below this much free disk
DISK_MIN_FREE_GB = float(env("SD_DISK_MIN_FREE_GB", "50"))
# The nightly database dumps (ops/pg-nightly-dump.sh)
BACKUP_DIR = Path(env("SD_BACKUP_DIR", str(Path.home() / "sd-backups")))

# Celery: Redis from Homebrew
CELERY_BROKER_URL = env("CELERY_BROKER_URL", "redis://localhost:6379/2")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", CELERY_BROKER_URL)
CELERY_TIMEZONE = TIME_ZONE
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TASK_ALWAYS_EAGER = env("CELERY_TASK_ALWAYS_EAGER", "0") == "1"
# Threads, not the default prefork pool. On macOS, prefork's pool processes are spawned
# rather than forked, start without Celery's task setup, and every task fails with "not
# enough values to unpack (expected 3, got 0)", on any Python version. Crawls and harvests
# mostly wait on the network anyway. For processes instead:
#   FORKED_BY_MULTIPROCESSING=1 CELERY_WORKER_POOL=prefork uv run celery -A config worker
CELERY_WORKER_POOL = env("CELERY_WORKER_POOL", "threads")
CELERY_WORKER_CONCURRENCY = 4
CELERY_BEAT_SCHEDULE = {
    "enqueue-due-work": {"task": "core.tasks.enqueue_due_work", "schedule": 60.0},
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "plain": {"format": "%(asctime)s %(levelname)-8s %(name)s: %(message)s"},
    },
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", "INFO")},
    "loggers": {
        "urllib3": {"level": "WARNING"},
        "pyogrio": {"level": "WARNING"},
        "django.db.backends": {"level": "WARNING"},
    },
}

UNFOLD = {
    "SITE_TITLE": "SD GIS Hub admin",
    "SITE_HEADER": "SD GIS Hub",
    "SITE_URL": "/",
}

NINJA_PAGINATION_PER_PAGE = 50
