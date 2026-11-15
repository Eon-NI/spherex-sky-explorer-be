import os
from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "eon_common.settings")
app = Celery("eon")
app.config_from_object("django.conf:settings", namespace="CELERY")
service = os.getenv("SERVICE_NAME", "observation")
if service in {"observation", "image_processing", "spectral"}:
    app.autodiscover_tasks([f"services.{service}.infrastructure"])
