from django.apps import AppConfig


class SocietiesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "societies"

    def ready(self):
        # Import signals to ensure they are registered
        from . import signals  # noqa: F401
