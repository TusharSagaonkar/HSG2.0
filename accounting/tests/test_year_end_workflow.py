from datetime import date
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError

from accounting.models import Account
from accounting.models import AccountCategory
from accounting.models import AccountingPeriod
from accounting.models import FinancialYear
from accounting.models import LedgerEntry
from accounting.models import PeriodStatusLog
from accounting.models import Voucher
from accounting.models import YearEndCloseLog
from accounting.services.period_workflow import close_period
from accounting.services.period_workflow import reopen_period
from accounting.services.year_end import close_financial_year_with_carry_forward
from housing.models import Society


@pytest.mark.django_db
def test_close_period_blocks_when_draft_exists():
    society = Society.objects.create(name="Draft Block Society")
    fy = FinancialYear.objects.create(
        society=society,
        name="FY 2024-25",
        start_date=date(2024, 4, 1),
        end_date=date(2025, 3, 31),
        is_open=True,
    )
    period = AccountingPeriod.objects.get(
        society=society,
        financial_year=fy,
        start_date=date(2024, 4, 1),
        end_date=date(2024, 4, 30),
    )
    Voucher.objects.create(
        society=society,
        voucher_type=Voucher.VoucherType.GENERAL,
        voucher_date=date(2024, 4, 10),
        narration="Draft in period",
    )

    with pytest.raises(ValidationError):
        close_period(period)


@pytest.mark.django_db
def test_close_and_reopen_period_creates_audit_logs():
    society = Society.objects.create(name="Period Audit Society")
    fy = FinancialYear.objects.create(
        society=society,
        name="FY 2024-25",
        start_date=date(2024, 4, 1),
        end_date=date(2025, 3, 31),
        is_open=True,
    )
    p1 = AccountingPeriod.objects.get(
        society=society,
        financial_year=fy,
        start_date=date(2024, 4, 1),
        end_date=date(2024, 4, 30),
    )
    p2 = AccountingPeriod.objects.get(
        society=society,
        financial_year=fy,
        start_date=date(2024, 5, 1),
        end_date=date(2024, 5, 31),
    )

    next_period = close_period(p1, reason="Close for month-end")
    assert next_period == p2
    assert PeriodStatusLog.objects.filter(period=p1, action=PeriodStatusLog.Action.CLOSED).exists()
    assert PeriodStatusLog.objects.filter(period=p2, action=PeriodStatusLog.Action.OPENED).exists()

    reopen_period(p1, reason="Reopen for adjustment")
    p1.refresh_from_db()
    p2.refresh_from_db()
    assert p1.is_open is True
    assert p2.is_open is False
    assert PeriodStatusLog.objects.filter(period=p1, action=PeriodStatusLog.Action.OPENED).exists()


@pytest.mark.django_db
def test_year_end_carry_forward_creates_opening_voucher_and_log():
    society = Society.objects.create(name="Year End Society")
    fy = FinancialYear.objects.create(
        society=society,
        name="FY 2024-25",
        start_date=date(2024, 4, 1),
        end_date=date(2025, 3, 31),
        is_open=True,
    )
    AccountingPeriod.objects.filter(
        society=society,
        financial_year=fy,
        start_date=date(2024, 4, 1),
        end_date=date(2024, 4, 30),
    ).update(is_open=True)

    asset_cat, _ = AccountCategory.objects.get_or_create(
        society=society,
        name="Current Assets",
        account_type="ASSET",
    )
    income_cat, _ = AccountCategory.objects.get_or_create(
        society=society,
        name="Income",
        account_type="INCOME",
    )
    cash = Account.objects.create(society=society, name="Cash", category=asset_cat)
    income = Account.objects.create(society=society, name="Maintenance Income", category=income_cat)

    voucher = Voucher.objects.create(
        society=society,
        voucher_type=Voucher.VoucherType.GENERAL,
        voucher_date=date(2024, 4, 10),
        narration="Year activity",
    )
    LedgerEntry.objects.create(voucher=voucher, account=cash, debit=Decimal("1000.00"))
    LedgerEntry.objects.create(voucher=voucher, account=income, credit=Decimal("1000.00"))
    voucher.post()

    next_fy, opening_voucher = close_financial_year_with_carry_forward(fy, notes="FY close test")

    fy.refresh_from_db()
    assert fy.is_open is False
    assert next_fy.start_date == date(2025, 4, 1)
    assert opening_voucher.voucher_type == Voucher.VoucherType.OPENING
    assert opening_voucher.posted_at is not None
    assert opening_voucher.voucher_date == next_fy.start_date
    assert YearEndCloseLog.objects.filter(source_financial_year=fy, opening_voucher=opening_voucher).exists()

    lines = opening_voucher.entries.order_by("id")
    assert lines.count() == 2
    assert lines.filter(account=cash, debit=Decimal("1000.00"), credit=Decimal("0.00")).exists()
    assert lines.filter(account=income, debit=Decimal("0.00"), credit=Decimal("1000.00")).exists()


@pytest.mark.django_db
def test_year_end_carry_forward_cannot_run_twice():
    society = Society.objects.create(name="Year End Idempotency Society")
    fy = FinancialYear.objects.create(
        society=society,
        name="FY 2024-25",
        start_date=date(2024, 4, 1),
        end_date=date(2025, 3, 31),
        is_open=True,
    )
    AccountingPeriod.objects.filter(
        society=society,
        financial_year=fy,
        start_date=date(2024, 4, 1),
        end_date=date(2024, 4, 30),
    ).update(is_open=True)

    asset_cat, _ = AccountCategory.objects.get_or_create(
        society=society,
        name="Current Assets",
        account_type="ASSET",
    )
    income_cat, _ = AccountCategory.objects.get_or_create(
        society=society,
        name="Income",
        account_type="INCOME",
    )
    cash = Account.objects.create(society=society, name="Cash", category=asset_cat)
    income = Account.objects.create(society=society, name="Maintenance Income", category=income_cat)
    voucher = Voucher.objects.create(
        society=society,
        voucher_type=Voucher.VoucherType.GENERAL,
        voucher_date=date(2024, 4, 10),
        narration="Year activity",
    )
    LedgerEntry.objects.create(voucher=voucher, account=cash, debit=Decimal("1.00"))
    LedgerEntry.objects.create(voucher=voucher, account=income, credit=Decimal("1.00"))
    voucher.post()

    close_financial_year_with_carry_forward(fy)
    with pytest.raises(ValidationError):
        close_financial_year_with_carry_forward(fy)
