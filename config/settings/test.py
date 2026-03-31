from .base import *  # noqa: F403
from .base import DATABASES
from .base import PASSWORD_HASHERS

DEBUG = False
SECRET_KEY = "test-secret-key"
TEST_RUNNER = "django.test.runner.DiscoverRunner"

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
    *PASSWORD_HASHERS,
]

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "test-cache",
    },
}

for database_alias, database_config in DATABASES.items():
    if not database_config:
        continue
    database_config["ATOMIC_REQUESTS"] = False
    database_config["CONN_MAX_AGE"] = 0

