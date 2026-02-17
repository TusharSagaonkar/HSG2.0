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

    earlier_open = AccountingPeriod.objects.filter(
        society=period.society,
        financial_year=period.financial_year,
        start_date__lt=period.start_date,
        is_open=True,
    ).exists()
    if earlier_open:
        raise ValidationError(
            "Cannot close this period while earlier periods are still open."
        )

    _ensure_no_draft_vouchers(period)

    next_period = AccountingPeriod.objects.filter(
        society=period.society,
        financial_year=period.financial_year,
        start_date__gt=period.end_date,
    ).order_by("start_date").first()

    with transaction.atomic():
        period.is_open = False
        period.save(update_fields=["is_open"])
        PeriodStatusLog.objects.create(
            period=period,
            action=PeriodStatusLog.Action.CLOSED,
            reason=reason,
            performed_by=performed_by,
        )

        if next_period:
            next_period.is_open = True
            next_period.save(update_fields=["is_open"])
            PeriodStatusLog.objects.create(
                period=next_period,
                action=PeriodStatusLog.Action.OPENED,
                reason=f"Auto-open after closing {period.start_date} to {period.end_date}",
                performed_by=performed_by,
            )
        else:
            period.financial_year.is_open = False
            period.financial_year.save(update_fields=["is_open"])

    return next_period


def reopen_period(period, *, performed_by=None, reason=""):
    if period.is_open:
        raise ValidationError("Selected period is already open.")

    later_open_periods = list(
        AccountingPeriod.objects.filter(
            society=period.society,
            financial_year=period.financial_year,
            start_date__gt=period.start_date,
            is_open=True,
        ).order_by("start_date")
    )

    if len(later_open_periods) > 1:
        raise ValidationError(
            "Cannot reopen period while multiple later periods are open."
        )

    with transaction.atomic():
        if later_open_periods:
            later_period = later_open_periods[0]
            later_period.is_open = False
            later_period.save(update_fields=["is_open"])
            PeriodStatusLog.objects.create(
                period=later_period,
                action=PeriodStatusLog.Action.CLOSED,
                reason=f"Auto-close while reopening {period.start_date} to {period.end_date}",
                performed_by=performed_by,
            )

        period.is_open = True
        period.save(update_fields=["is_open"])
        PeriodStatusLog.objects.create(
            period=period,
            action=PeriodStatusLog.Action.OPENED,
            reason=reason,
            performed_by=performed_by,
        )

        if not period.financial_year.is_open:
            period.financial_year.is_open = True
            period.financial_year.save(update_fields=["is_open"])

