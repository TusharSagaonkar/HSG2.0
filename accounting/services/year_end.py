from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from accounting.models import Account
from accounting.models import AccountingPeriod
from accounting.models import FinancialYear
from accounting.models import LedgerEntry
from accounting.models import PeriodStatusLog
from accounting.models import Voucher
from accounting.models import YearEndCloseLog
from accounting.services.reporting import build_trial_balance


def _default_next_year_name(financial_year):
    start_year = financial_year.end_date.year
    end_year = start_year + 1
    return f"FY {start_year}-{str(end_year)[-2:]}"


def _ensure_no_unposted_in_year(financial_year):
    has_drafts = Voucher.objects.filter(
        society=financial_year.society,
        voucher_date__gte=financial_year.start_date,
        voucher_date__lte=financial_year.end_date,
        posted_at__isnull=True,
    ).exists()
    if has_drafts:
        raise ValidationError(
            "Cannot close financial year while draft vouchers exist in that year."
        )


def close_financial_year_with_carry_forward(financial_year, *, performed_by=None, notes=""):
    if YearEndCloseLog.objects.filter(source_financial_year=financial_year).exists():
        raise ValidationError("Year-end carry-forward already completed for this year.")

    _ensure_no_unposted_in_year(financial_year)

    open_periods = list(
        AccountingPeriod.objects.filter(
            society=financial_year.society,
            financial_year=financial_year,
            is_open=True,
        ).order_by("start_date")
    )

    # Close all open periods in bulk with individual audit logs.
    if open_periods:
        for period in open_periods:
            with transaction.atomic():
                period.is_open = False
                period.save(update_fields=["is_open"])
                PeriodStatusLog.objects.create(
                    period=period,
                    action=PeriodStatusLog.Action.CLOSED,
                    reason=f"Year-end close for {financial_year.name}",
                    performed_by=performed_by,
                )

        # If the FY was closed by last-period logic, this is idempotent.
        financial_year.is_open = False
        financial_year.save(update_fields=["is_open"])

    duration = financial_year.end_date - financial_year.start_date
    next_start = financial_year.end_date + timedelta(days=1)
    next_end = next_start + duration
    next_financial_year, _ = FinancialYear.objects.get_or_create(
        society=financial_year.society,
        start_date=next_start,
        end_date=next_end,
        defaults={
            "name": _default_next_year_name(financial_year),
            "is_open": True,
        },
    )

    trial = build_trial_balance(
        society=financial_year.society,
        financial_year=financial_year,
    )
    rows = [row for row in trial["rows"] if row["balance_amount"] != Decimal("0.00")]
    if len(rows) < 2:
        raise ValidationError("Insufficient non-zero balances to create opening voucher.")

    with transaction.atomic():
        opening_voucher = Voucher.objects.create(
            society=financial_year.society,
            voucher_type=Voucher.VoucherType.OPENING,
            voucher_date=next_financial_year.start_date,
            narration=f"Opening balances carried from {financial_year.name}",
        )
        for row in rows:
            account = Account.objects.get(pk=row["account_id"], society=financial_year.society)
            debit = row["balance_amount"] if row["balance_side"] == "DR" else Decimal("0.00")
            credit = row["balance_amount"] if row["balance_side"] == "CR" else Decimal("0.00")
            LedgerEntry.objects.create(
                voucher=opening_voucher,
                account=account,
                debit=debit,
                credit=credit,
            )
        opening_voucher.post()

        YearEndCloseLog.objects.create(
            source_financial_year=financial_year,
            target_financial_year=next_financial_year,
            opening_voucher=opening_voucher,
            performed_by=performed_by,
            notes=notes,
        )

    return next_financial_year, opening_voucher
