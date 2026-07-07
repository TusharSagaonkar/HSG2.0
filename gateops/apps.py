from django.apps import AppConfig


class GateOpsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "gateops"
    verbose_name = "Gate Operations"

    def ready(self):
        import gateops.signals  # noqa: F401
