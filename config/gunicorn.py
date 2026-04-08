"""Gunicorn configuration for the Housing Accounting Django app."""

from __future__ import annotations

import multiprocessing
import os


def _default_workers() -> int:
    """Use a conservative default while allowing env overrides."""
    return max(2, min(4, multiprocessing.cpu_count()))


bind = os.getenv("GUNICORN_BIND", f"0.0.0.0:{os.getenv('PORT', '8000')}")
workers = int(os.getenv("WEB_CONCURRENCY", _default_workers()))
worker_class = os.getenv("GUNICORN_WORKER_CLASS", "sync")
threads = int(os.getenv("GUNICORN_THREADS", "1"))
timeout = int(os.getenv("GUNICORN_TIMEOUT", "60"))
graceful_timeout = int(os.getenv("GUNICORN_GRACEFUL_TIMEOUT", "30"))
keepalive = int(os.getenv("GUNICORN_KEEPALIVE", "5"))
accesslog = os.getenv("GUNICORN_ACCESSLOG", "-")
errorlog = os.getenv("GUNICORN_ERRORLOG", "-")
capture_output = True
loglevel = os.getenv("GUNICORN_LOG_LEVEL", "info")

# Manual Gunicorn runs should behave like manage.py and default to local settings.
raw_env = [
    f"DJANGO_SETTINGS_MODULE={os.getenv('DJANGO_SETTINGS_MODULE', 'config.settings.local')}",
]
