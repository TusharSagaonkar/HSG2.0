from .base import *  # noqa: F403
from .base import BASE_DIR
from .base import INSTALLED_APPS
from .base import MIDDLEWARE
from .base import env
from django.core.exceptions import ImproperlyConfigured

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
ALLOWED_HOSTS = ["localhost", "0.0.0.0", "127.0.0.1","TusharSagaonkar.pythonanywhere.com"]  # noqa: S104

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

SUPABASE_DATABASE_URL = env.str("SUPABASE_DATABASE_URL", default="")
if not SUPABASE_DATABASE_URL:
    supabase_password = env.str("SUPABASE_DB_PASSWORD", default="")
    if not supabase_password:
        message = (
            "Supabase is configured as primary database. Set SUPABASE_DATABASE_URL "
            "or SUPABASE_DB_PASSWORD in environment."
        )
        raise ImproperlyConfigured(message)
    SUPABASE_DATABASE_URL = (
        "postgresql://postgres:"
        f"{supabase_password}@"
        "db.wmixkdfdfsoawucdbfsc.supabase.co:5432/postgres?sslmode=require"
    )

DATABASES = {
    "default": env.db_url_config(SUPABASE_DATABASE_URL),
    "local": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "housing_accounting",
        "USER": "tushar",
        "PASSWORD": "tushar",
        "HOST": "localhost",
        "PORT": "5432",
        "OPTIONS": {
            "options": "-c default_transaction_read_only=on",
        },
    },
}
DATABASES["default"]["ATOMIC_REQUESTS"] = True
DATABASES["local"]["ATOMIC_REQUESTS"] = True
