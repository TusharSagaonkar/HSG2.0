from django.core.management.base import BaseCommand

from notifications.services import ensure_default_email_templates


class Command(BaseCommand):
    help = "Create or refresh the default email templates used by the platform."

    def handle(self, *args, **options):
        templates = ensure_default_email_templates()
        self.stdout.write(
            self.style.SUCCESS(
                f"Bootstrapped {len(templates)} email templates.",
            ),
        )
        for template in templates:
            self.stdout.write(f"- {template.template_name}")
