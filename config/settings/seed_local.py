"""Settings for seeding the Jagruti demo into a temporary local SQLite database.

Used only by ``manage.py`` invocations that pass ``--settings=config.settings.seed_local``
so the demo can be generated locally and bulk-copied to a remote database.
"""

from .base import *  # noqa: F403
from .base import DATABASES

DEBUG = False

DATABASES["default"] = {
    "ENGINE": "django.db.backends.sqlite3",
    "NAME": "/tmp/jagruti_seed.sqlite3",
    "ATOMIC_REQUESTS": False,
    "AUTOCOMMIT": True,
    "CONN_MAX_AGE": 0,
    "CONN_HEALTH_CHECKS": False,
    "OPTIONS": {},
    "TIME_ZONE": None,
    "USER": "",
    "PASSWORD": "",
    "HOST": "",
    "PORT": "",
    "TEST": {
        "CHARSET": None,
        "COLLATION": None,
        "MIGRATE": True,
        "MIRROR": None,
        "NAME": None,
    },
}

# Avoid any SMTP attempts while generating demo data.
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
