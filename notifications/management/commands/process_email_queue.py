from django.core.management.base import BaseCommand

from notifications.services import process_email_queue


class Command(BaseCommand):
    help = "Process pending and retryable emails from the email queue."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=None)

    def handle(self, *args, **options):
        processed = process_email_queue(limit=options["limit"])
        self.stdout.write(self.style.SUCCESS(f"Processed {processed} queued emails."))
