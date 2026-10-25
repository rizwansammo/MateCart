"""Celery application for the commerce API. Tasks are discovered from installed Django apps."""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")

app = Celery("commerce")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
