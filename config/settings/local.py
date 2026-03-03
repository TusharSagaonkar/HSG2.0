from copy import deepcopy

from .base import *  # noqa: F403
from .base import BASE_DIR
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

# Your stuff...
# ------------------------------------------------------------------------------
local_env_file = BASE_DIR / ".env.local"
if local_env_file.exists():
    env.read_env(str(local_env_file))

local_database = {
    "ENGINE": "django.db.backends.postgresql",
    "NAME": env.str("LOCAL_DB_NAME", default="housing_accounting"),
    "USER": env.str("LOCAL_DB_USER", default="tushar"),
    "PASSWORD": env.str("LOCAL_DB_PASSWORD", default="tushar"),
    "HOST": env.str("LOCAL_DB_HOST", default="localhost"),
    "PORT": env.str("LOCAL_DB_PORT", default="5432"),
}

USE_SUPABASE_DB = env.bool("USE_SUPABASE_DB", default=False)
SUPABASE_DATABASE_URL = env.str("SUPABASE_DATABASE_URL", default="")
if USE_SUPABASE_DB and not SUPABASE_DATABASE_URL:
    supabase_password = env.str("SUPABASE_DB_PASSWORD", default="")
    if supabase_password:
        SUPABASE_DATABASE_URL = (
            "postgresql://postgres:"
            f"{supabase_password}@"
            "db.wmixkdfdfsoawucdbfsc.supabase.co:5432/postgres?sslmode=require"
        )
SUPABASE_POOLER_URL = env.str("SUPABASE_POOLER_URL", default="")
supabase_target_url = SUPABASE_POOLER_URL or SUPABASE_DATABASE_URL

DATABASES = {
    "default": deepcopy(local_database),
    "local": deepcopy(local_database),
}

if USE_SUPABASE_DB and supabase_target_url:
    DATABASES["supabase"] = env.db_url_config(supabase_target_url)
    DATABASES["supabase"].setdefault("OPTIONS", {})
    DATABASES["supabase"]["OPTIONS"]["sslmode"] = "require"

DATABASES["default"]["ATOMIC_REQUESTS"] = True
DATABASES["local"]["ATOMIC_REQUESTS"] = True
local_conn_max_age = env.int("LOCAL_DB_CONN_MAX_AGE", default=300)
DATABASES["default"]["CONN_MAX_AGE"] = local_conn_max_age
DATABASES["local"]["CONN_MAX_AGE"] = local_conn_max_age
DATABASES["default"]["CONN_HEALTH_CHECKS"] = True
DATABASES["local"]["CONN_HEALTH_CHECKS"] = True
local_connect_timeout = env.int("LOCAL_DB_CONNECT_TIMEOUT", default=5)
DATABASES["default"].setdefault("OPTIONS", {})
DATABASES["default"]["OPTIONS"]["connect_timeout"] = local_connect_timeout
DATABASES["local"].setdefault("OPTIONS", {})
DATABASES["local"]["OPTIONS"]["connect_timeout"] = local_connect_timeout
if "supabase" in DATABASES:
    DATABASES["supabase"]["ATOMIC_REQUESTS"] = True
    DATABASES["supabase"]["CONN_MAX_AGE"] = env.int(
        "SUPABASE_CONN_MAX_AGE",
        default=300,
    )
    DATABASES["supabase"]["CONN_HEALTH_CHECKS"] = True
    DATABASES["supabase"]["DISABLE_SERVER_SIDE_CURSORS"] = True
    DATABASES["supabase"]["OPTIONS"]["connect_timeout"] = env.int(
        "SUPABASE_CONNECT_TIMEOUT",
        default=5,
    )
