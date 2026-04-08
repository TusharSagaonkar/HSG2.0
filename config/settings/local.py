import dj_database_url
import psycopg

from .base import *  # noqa: F403
from .base import BASE_DIR
from .base import DATABASES
from .base import INSTALLED_APPS
from .base import MIDDLEWARE
from .base import env

# GENERAL
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#debug
DEBUG = True
# https://docs.djangoproject.com/en/dev/ref/settings/#secret-key
SECRET_KEY = env("DJANGO_SECRET_KEY")
# https://docs.djangoproject.com/en/dev/ref/settings/#allowed-hosts
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS")

# CACHES
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#caches
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "",
    },
}

# EMAIL
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#email-backend
EMAIL_BACKEND = env(
    "DJANGO_EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend",
)
if (
    EMAIL_BACKEND == "django.core.mail.backends.smtp.EmailBackend"
    and not env("DJANGO_EMAIL_HOST", default="").strip()
):
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# WhiteNoise
# ------------------------------------------------------------------------------
# http://whitenoise.evans.io/en/latest/django.html#using-whitenoise-in-development
INSTALLED_APPS = ["whitenoise.runserver_nostatic", *INSTALLED_APPS]


# django-debug-toolbar
# ------------------------------------------------------------------------------
# https://django-debug-toolbar.readthedocs.io/en/latest/installation.html#prerequisites
INSTALLED_APPS += ["debug_toolbar"]
# https://django-debug-toolbar.readthedocs.io/en/latest/installation.html#middleware
MIDDLEWARE += ["debug_toolbar.middleware.DebugToolbarMiddleware"]
# https://django-debug-toolbar.readthedocs.io/en/latest/configuration.html#debug-toolbar-config
DEBUG_TOOLBAR_CONFIG = {
    "DISABLE_PANELS": [
        "debug_toolbar.panels.redirects.RedirectsPanel",
        # Disable profiling panel due to an issue with Python 3.12+:
        # https://github.com/jazzband/django-debug-toolbar/issues/1875
        "debug_toolbar.panels.profiling.ProfilingPanel",
    ],
    "SHOW_TEMPLATE_CONTEXT": True,
}
# https://django-debug-toolbar.readthedocs.io/en/latest/installation.html#internal-ips
INTERNAL_IPS = ["127.0.0.1", "10.0.2.2"]


# django-extensions
# ------------------------------------------------------------------------------
# https://django-extensions.readthedocs.io/en/latest/installation_instructions.html#configuration
INSTALLED_APPS += ["django_extensions"]

LOCAL_DATABASE_MODE = env.str("DJANGO_LOCAL_DATABASE_MODE", default="auto").lower()

def _remote_database_is_reachable() -> bool:
    database_url = env.str("DATABASE_URL", default="")
    if not database_url:
        return False

    try:
        with psycopg.connect(
            database_url,
            connect_timeout=env.int("DATABASE_CONNECT_TIMEOUT", default=2),
        ):
            return True
    except psycopg.Error:
        return False


if LOCAL_DATABASE_MODE == "sqlite" or (
    LOCAL_DATABASE_MODE == "auto" and not _remote_database_is_reachable()
):
    DATABASES["default"] = dj_database_url.parse(
        f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
        conn_max_age=0,
    )

for database_alias, env_prefix in (
    ("default", "DATABASE"),
    ("analytics", "ANALYTICS_DB"),
    ("archive", "ARCHIVE_DB"),
):
    if not DATABASES.get(database_alias):
        continue

    DATABASES[database_alias]["CONN_MAX_AGE"] = env.int(
        f"{env_prefix}_CONN_MAX_AGE",
        default=300,
    )
    DATABASES[database_alias]["CONN_HEALTH_CHECKS"] = True
    DATABASES[database_alias]["DISABLE_SERVER_SIDE_CURSORS"] = env.bool(
        f"{env_prefix}_DISABLE_SERVER_SIDE_CURSORS",
        default=database_alias == "default",
    )
    if DATABASES[database_alias]["ENGINE"] != "django.db.backends.sqlite3":
        DATABASES[database_alias].setdefault("OPTIONS", {})
        DATABASES[database_alias]["OPTIONS"]["connect_timeout"] = env.int(
            f"{env_prefix}_CONNECT_TIMEOUT",
            default=5,
        )
