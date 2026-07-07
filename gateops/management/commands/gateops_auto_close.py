"""Management command to auto-close stale GateEvents.

Scheduled (e.g. via Celery beat or cron) to close GateEvents whose visitor has
entered but never exited past their ``auto_close_at`` timestamp. This keeps the
"visitors currently inside" count accurate and prevents zombie sessions.

Usage::

    python manage.py gateops_auto_close
    python manage.py gateops_auto_close --society Deepsagar
    python manage.py gateops_auto_close --society 3 --dry-run
"""

from django.core.management.base import BaseCommand
from django.utils import timezone

from gateops.models import GateEvent
from gateops.services.gate_event_lifecycle import GateEventLifecycleService


class Command(BaseCommand):
    help = "Auto-close GateEvents that have exceeded their auto_close_at timestamp."

    def add_arguments(self, parser):
        parser.add_argument(
            "--society",
            type=str,
            default=None,
            help="Limit auto-close to a specific society (name or ID).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            default=False,
            help="Show what would be closed without actually closing.",
        )

    def handle(self, *args, **options):
        now = timezone.now()
        qs = GateEvent.objects.filter(status="entered", auto_close_at__lte=now)
        if options["society"]:
            # Try by ID first, then by name.
            try:
                society_id = int(options["society"])
                qs = qs.filter(society_id=society_id)
            except (ValueError, TypeError):
                qs = qs.filter(society__name__iexact=options["society"])

        count = qs.count()
        if count == 0:
            self.stdout.write(self.style.SUCCESS("No events to auto-close."))
            return

        self.stdout.write(f"Found {count} event(s) to auto-close.")

        closed = 0
        errors = 0
        for event in qs.select_related("society", "person"):
            try:
                if options["dry_run"]:
                    self.stdout.write(
                        f"  [DRY RUN] Would close: {event.event_uuid} "
                        f"(entered_at={event.entered_at}, auto_close_at={event.auto_close_at})"
                    )
                else:
                    GateEventLifecycleService.auto_close(event)
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"  Closed: {event.event_uuid} "
                            f"(entered_at={event.entered_at}, auto_close_at={event.auto_close_at})"
                        )
                    )
                closed += 1
            except Exception as exc:  # noqa: BLE001 — keep processing remaining events.
                self.stdout.write(
                    self.style.ERROR(f"  Failed to close {event.event_uuid}: {exc}")
                )
                errors += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. {closed} event(s) {'would be ' if options['dry_run'] else ''}closed, {errors} error(s)."
            )
        )
