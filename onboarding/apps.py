from django.apps import AppConfig


class OnboardingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "onboarding"
    verbose_name = "Society Onboarding & Migration Wizard"

    def ready(self):
        # Import signals if needed (e.g., auto-create wizard step logs).
        pass
