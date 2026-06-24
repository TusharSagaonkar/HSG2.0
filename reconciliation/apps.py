from django.apps import AppConfig


class ReconciliationConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "reconciliation"

    def ready(self):
        import reconciliation.signals  # noqa: F401 — register signal handlers
