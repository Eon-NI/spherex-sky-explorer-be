import os
from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "eon_common.settings")
app = Celery("eon")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks(["services.observation.infrastructure"])
