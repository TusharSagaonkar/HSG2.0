from .base import *  # noqa: F403
from .base import DATABASES
from .base import INSTALLED_APPS
from .base import MIDDLEWARE
from .base import env

# GENERAL
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#debug
DEBUG = True
# https://docs.djangoproject.com/en/dev/ref/settings/#secret-key
SECRET_KEY = env(
    "DJANGO_SECRET_KEY",
    default="Zg4nsHPBxs1fcLULFUjaL1dTb3xIsfAbaxZHj4xgip042lf769nnJjuHUuinhhEG",
)
# https://docs.djangoproject.com/en/dev/ref/settings/#allowed-hosts
ALLOWED_HOSTS = [
    "localhost",
    "0.0.0.0",  # noqa: S104
    "127.0.0.1",
    "TusharSagaonkar.pythonanywhere.com",
    "hsg2-0.onrender.com",
]

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
    DATABASES[database_alias].setdefault("OPTIONS", {})
    DATABASES[database_alias]["OPTIONS"]["connect_timeout"] = env.int(
        f"{env_prefix}_CONNECT_TIMEOUT",
        default=5,
    )
