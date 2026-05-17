from django.apps import AppConfig


class MembersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "members"

    def ready(self):
        # Import signals to ensure they are connected
        try:
            import members.signals  # noqa: F401
        except ImportError:
            # Signals module may not exist yet
            pass
