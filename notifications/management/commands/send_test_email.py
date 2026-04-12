from django.conf import settings
from django.core.management.base import BaseCommand

from notifications.models import EmailQueue
from notifications.services import ensure_default_email_templates
from notifications.services import queue_email


class Command(BaseCommand):
    help = "Queue and send a test email using the standard email pipeline."

    def add_arguments(self, parser):
        parser.add_argument("--to", required=True, help="Recipient email address.")

    def handle(self, *args, **options):
        recipient_email = options["to"]
        ensure_default_email_templates()
        queue_item = queue_email(
            recipient_email=recipient_email,
            template_name="system.test_email",
            context={
                "recipient_email": recipient_email,
                "triggered_at": self._now_text(),
                "environment_name": settings.SETTINGS_MODULE,
            },
            email_type=EmailQueue.EmailType.OTHER,
            process_now=True,
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Email queue item #{queue_item.id} finished with status {queue_item.status}.",
            ),
        )
        self.stdout.write(f"Template: {queue_item.template.template_name}")
        self.stdout.write(f"Recipient: {queue_item.recipient_email}")
        self.stdout.write(f"Error: {queue_item.error_message or '-'}")

    def _now_text(self):
        from django.utils import timezone

        return timezone.localtime().isoformat()
