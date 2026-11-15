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
ALLOWED_HOSTS = list(dict.fromkeys(
    [host.strip() for host in os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")]
    + list(SERVICE_APPS)
))
ROOT_URLCONF = "eon_common.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {},
    }
]
INSTALLED_APPS = [
    "django.contrib.auth", "django.contrib.contenttypes", "rest_framework",
    "drf_spectacular", SERVICE_APPS[SERVICE_NAME],
]
MIDDLEWARE = ["django.middleware.security.SecurityMiddleware", "eon_common.cors.SpectralCorsMiddleware",
              "django.middleware.common.CommonMiddleware"]
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
        "schedule": 86400.0,
    }
} if SERVICE_NAME == "observation" else {}
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"json": {"()": "pythonjsonlogger.json.JsonFormatter", "format": "%(asctime)s %(levelname)s %(name)s %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "json"}},
    "root": {"handlers": ["console"], "level": os.getenv("LOG_LEVEL", "INFO")},
}

EON_TAP_MAX_RESULTS = int(os.getenv("EON_TAP_MAX_RESULTS", "100"))
EON_MAX_CUTOUT_BYTES = int(os.getenv("EON_MAX_CUTOUT_BYTES", str(128 * 1024 * 1024)))
EON_INTERNAL_TOKEN = os.getenv("EON_INTERNAL_TOKEN", "")
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 300
if SERVICE_NAME == "spectral":
    CELERY_WORKER_PREFETCH_MULTIPLIER = 1
EON_MAX_SPECTRAL_CUBE_BYTES = int(os.getenv("EON_MAX_SPECTRAL_CUBE_BYTES", str(400 * 1024 * 1024)))
EON_SPECTRAL_DOWNLOAD_WORKERS = int(os.getenv("EON_SPECTRAL_DOWNLOAD_WORKERS", "4"))
EON_SPECTRAL_CACHE_MAX_BYTES = int(os.getenv("EON_SPECTRAL_CACHE_MAX_BYTES", str(700 * 1024 * 1024)))
EON_SPECTRAL_CACHE_DIR = os.getenv("EON_SPECTRAL_CACHE_DIR", "/tmp/eon-spectral-cubes")

CELERY_RESULT_EXPIRES = 7 * 24 * 3600

EON_OBSERVATION_CACHE_TTL_SECONDS = int(os.getenv("EON_OBSERVATION_CACHE_TTL_SECONDS", "21600"))

EON_MAX_FOV_DEG = float(os.getenv("EON_MAX_FOV_DEG", "2"))
EON_MAX_CUTOUT_DEG = float(os.getenv("EON_MAX_CUTOUT_DEG", "1"))
CACHES = {"default": {"BACKEND": "django.core.cache.backends.redis.RedisCache",
                      "LOCATION": CELERY_BROKER_URL}}
REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"] = {
    "sia_search": "30/min", "tap_search": "10/min", "cutout_submit": "10/min",
    "metadata_read": "60/min", "job_status": "120/min", "fits_stream": "30/min",
    "spectral_submit": "30/min", "spectral_preview": "120/min",
}
