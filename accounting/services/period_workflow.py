from django.core.exceptions import ValidationError
from django.db import transaction

from accounting.models import AccountingPeriod
from accounting.models import PeriodStatusLog
from accounting.models import Voucher


def _ensure_no_draft_vouchers(period):
    has_drafts = Voucher.objects.filter(
        society=period.society,
        voucher_date__gte=period.start_date,
        voucher_date__lte=period.end_date,
        posted_at__isnull=True,
    ).exists()
    if has_drafts:
        raise ValidationError(
            "Cannot close period while draft vouchers exist in this period."
        )


def close_period(period, *, performed_by=None, reason=""):
    if not period.is_open:
        raise ValidationError("Selected period is already closed.")

    _ensure_no_draft_vouchers(period)

    is_last_period = not AccountingPeriod.objects.filter(
        society=period.society,
        financial_year=period.financial_year,
        start_date__gt=period.end_date,
    ).exists()

    with transaction.atomic():
        period.is_open = False
        period.save(update_fields=["is_open"])
        PeriodStatusLog.objects.create(
            period=period,
            action=PeriodStatusLog.Action.CLOSED,
            reason=reason,
            performed_by=performed_by,
        )

        # If this was the last period in the FY and the FY is still open, close it.
        if is_last_period and period.financial_year.is_open:
            period.financial_year.is_open = False
            period.financial_year.save(update_fields=["is_open"])

    return None


def reopen_period(period, *, performed_by=None, reason=""):
    if period.is_open:
        raise ValidationError("Selected period is already open.")

    with transaction.atomic():
        period.is_open = True
        period.save(update_fields=["is_open"])
        PeriodStatusLog.objects.create(
            period=period,
            action=PeriodStatusLog.Action.OPENED,
            reason=reason,
            performed_by=performed_by,
        )

        # If the financial year was closed, re-open it.
        if not period.financial_year.is_open:
            period.financial_year.is_open = True
            period.financial_year.save(update_fields=["is_open"])

