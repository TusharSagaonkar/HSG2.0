"""
Management command to auto-open the accounting period containing today's date
for every society with an open financial year.

Designed to be run daily via cron.  Idempotent — if the relevant period is
already open the command simply logs and skips.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from django.utils.timezone import localdate

from accounting.models import AccountingPeriod
from accounting.models import FinancialYear
from accounting.models import PeriodStatusLog


class Command(BaseCommand):
    help = (
        "Auto-open the accounting period that contains today's date "
        "for every society whose financial year is still open."
    )

    def handle(self, *args, **options):
        today = localdate()

        # Every society that has at least one open financial year.
        open_fy_societies = (
            FinancialYear.objects.filter(is_open=True)
            .values_list("society_id", flat=True)
            .distinct()
        )

        opened_count = 0
        skipped_count = 0

        for society_id in open_fy_societies:
            # Find the period that contains *today* within an open FY of this society.
            period = (
                AccountingPeriod.objects.filter(
                    society_id=society_id,
                    financial_year__is_open=True,
                    start_date__lte=today,
                    end_date__gte=today,
                )
                .select_related("financial_year")
                .first()
            )

            if period is None:
                # No period covers today for this society – nothing to do.
                continue

            if period.is_open:
                skipped_count += 1
                continue

            with transaction.atomic():
                period.is_open = True
                period.save(update_fields=["is_open"])
                PeriodStatusLog.objects.create(
                    period=period,
                    action=PeriodStatusLog.Action.OPENED,
                    reason="Auto-opened by system clock",
                    performed_by=None,
                )
                opened_count += 1

        self.stdout.write(
            f"auto_open_period: {opened_count} opened, "
            f"{skipped_count} already open."
        )