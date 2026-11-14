"""One deployable Django process per bounded context."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SERVICE_NAME = os.getenv("SERVICE_NAME", "observation")
SERVICE_APPS = {
    "observation": "services.observation",
    "image_processing": "services.image_processing",
    "spectral": "services.spectral",
    "jobs": "services.jobs",
}
if SERVICE_NAME not in SERVICE_APPS:
    raise ValueError(f"Unknown SERVICE_NAME: {SERVICE_NAME}")
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "development-only-insecure-key")
DEBUG = os.getenv("DJANGO_DEBUG", "false").lower() == "true"
ALLOWED_HOSTS = os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
ROOT_URLCONF = "eon_common.urls"
INSTALLED_APPS = [
    "django.contrib.auth", "django.contrib.contenttypes", "rest_framework",
    "drf_spectacular", SERVICE_APPS[SERVICE_NAME],
]
MIDDLEWARE = ["django.middleware.security.SecurityMiddleware", "django.middleware.common.CommonMiddleware"]
DATABASES = {"default": {
    "ENGINE": "django.db.backends.postgresql",
    "NAME": os.getenv("POSTGRES_DB", f"eon_{SERVICE_NAME}"),
    "USER": os.getenv("POSTGRES_USER", "eon"),
    "PASSWORD": os.getenv("POSTGRES_PASSWORD", ""),
    "HOST": os.getenv("POSTGRES_HOST", "localhost"),
    "PORT": os.getenv("POSTGRES_PORT", "5432"),
}}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
TIME_ZONE = "UTC"
REST_FRAMEWORK = {
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "eon_common.errors.exception_handler",
}
SPECTACULAR_SETTINGS = {"TITLE": f"EON {SERVICE_NAME} API", "VERSION": "1.0.0"}
CELERY_BROKER_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = os.getenv("REDIS_URL", "redis://localhost:6379/0")
CELERY_BEAT_SCHEDULE = {
    "sync-irsa": {
        "task": "services.observation.infrastructure.tasks.sync_irsa",
        "schedule": 3600.0,
    }
} if SERVICE_NAME == "observation" else {}
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"json": {"()": "pythonjsonlogger.json.JsonFormatter", "format": "%(asctime)s %(levelname)s %(name)s %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "json"}},
    "root": {"handlers": ["console"], "level": os.getenv("LOG_LEVEL", "INFO")},
}
