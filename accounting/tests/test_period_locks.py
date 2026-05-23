from datetime import date
import pytest
from django.core.exceptions import ValidationError

from accounting.models.model_Voucher import Voucher
from accounting.models.model_LedgerEntry import LedgerEntry
from accounting.models.model_Account import Account
from accounting.models.model_AccountCategory import AccountCategory
from accounting.models.model_FinancialYear import FinancialYear
from accounting.models.model_AccountingPeriod import AccountingPeriod
from housing.models import Society


@pytest.mark.django_db
def test_posting_blocked_in_closed_financial_year():
    society = Society.objects.create(name="Test Society")

    FinancialYear.objects.create(
        society=society,
        name="FY 2023-24",
        start_date=date(2023, 4, 1),
        end_date=date(2024, 3, 31),
        is_open=False,
    )

    cat, _ = AccountCategory.objects.get_or_create(
        society=society,
        name="Cash",
        account_type="ASSET",
    )
    acc = Account.objects.create(society=society, name="Cash", category=cat)

    v = Voucher.objects.create(
        society=society,
        voucher_type="GENERAL",
        voucher_date=date(2024, 3, 31),
    )

    LedgerEntry.objects.create(voucher=v, account=acc, debit=5)
    LedgerEntry.objects.create(voucher=v, account=acc, credit=5)

    with pytest.raises(ValidationError):
        v.post()


@pytest.mark.django_db
def test_posting_blocked_in_closed_month():
    society = Society.objects.create(name="Test Society")

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
        start_date=date(2024, 8, 1),
        end_date=date(2024, 8, 31),
    ).update(is_open=False)

    cat, _ = AccountCategory.objects.get_or_create(
        society=society,
        name="Cash",
        account_type="ASSET",
    )
    acc = Account.objects.create(society=society, name="Cash", category=cat)

    v = Voucher.objects.create(
        society=society,
        voucher_type="GENERAL",
        voucher_date=date(2024, 8, 6),
    )

    LedgerEntry.objects.create(voucher=v, account=acc, debit=15)
    LedgerEntry.objects.create(voucher=v, account=acc, credit=15)

    with pytest.raises(ValidationError):
        v.post()


@pytest.mark.django_db
def test_posting_uses_financial_year_of_same_society_only():
    society_closed = Society.objects.create(name="Closed Society")
    society_open = Society.objects.create(name="Open Society")

    FinancialYear.objects.create(
        society=society_closed,
        name="FY 2024-25 Closed",
        start_date=date(2024, 4, 1),
        end_date=date(2025, 3, 31),
        is_open=False,
    )
    fy_open = FinancialYear.objects.create(
        society=society_open,
        name="FY 2024-25 Open",
        start_date=date(2024, 4, 1),
        end_date=date(2025, 3, 31),
        is_open=True,
    )

    AccountingPeriod.objects.filter(
        society=society_closed,
        start_date=date(2024, 8, 1),
        end_date=date(2024, 8, 31),
    ).update(is_open=True)
    AccountingPeriod.objects.filter(
        society=society_open,
        financial_year=fy_open,
        start_date=date(2024, 8, 1),
        end_date=date(2024, 8, 31),
    ).update(is_open=True)

    cat, _ = AccountCategory.objects.get_or_create(
        society=society_closed,
        name="Cash",
        account_type="ASSET",
    )
    acc = Account.objects.create(society=society_closed, name="Cash", category=cat)

    v = Voucher.objects.create(
        society=society_closed,
        voucher_type="GENERAL",
        voucher_date=date(2024, 8, 6),
    )
    LedgerEntry.objects.create(voucher=v, account=acc, debit=10)
    LedgerEntry.objects.create(voucher=v, account=acc, credit=10)

    with pytest.raises(ValidationError):
        v.post()


@pytest.mark.django_db
def test_posting_allowed_in_open_period_when_earlier_period_closed():
    """
    Under cumulative multi-open, closing an earlier period does not
    prevent posting in a later open period.
    """
    society = Society.objects.create(name="Multi-Open Posting Society")
    fy = FinancialYear.objects.create(
        society=society,
        name="FY 2024-25",
        start_date=date(2024, 4, 1),
        end_date=date(2025, 3, 31),
        is_open=True,
    )

    # Close April but keep May open.
    AccountingPeriod.objects.filter(
        society=society,
        financial_year=fy,
        start_date=date(2024, 4, 1),
    ).update(is_open=False)

    cat, _ = AccountCategory.objects.get_or_create(
        society=society, name="Cash", account_type="ASSET",
    )
    income_cat, _ = AccountCategory.objects.get_or_create(
        society=society, name="Income", account_type="INCOME",
    )
    cash_acc = Account.objects.create(
        society=society, name="Cash", category=cat,
        account_type=Account.AccountType.ASSET,
    )
    income_acc = Account.objects.create(
        society=society, name="Income", category=income_cat,
        account_type=Account.AccountType.INCOME,
    )

    v = Voucher.objects.create(
        society=society,
        voucher_type="GENERAL",
        voucher_date=date(2024, 5, 10),
    )
    LedgerEntry.objects.create(voucher=v, account=cash_acc, debit=20)
    LedgerEntry.objects.create(voucher=v, account=income_acc, credit=20)

    # Should succeed — May is open despite April being closed.
    v.post()
    v.refresh_from_db()
    assert v.posted_at is not None


@pytest.mark.django_db
def test_multiple_periods_open_on_fy_creation(monkeypatch):
    """
    When a FinancialYear is created mid-year, all periods from
    start_date up to today are open (cumulative multi-open).
    """
    from django.utils.timezone import localdate

    society = Society.objects.create(name="Mid-Year Creation Society")
    fake_today = date(2025, 8, 10)
    monkeypatch.setattr(
        "accounting.models.model_FinancialYear.localdate",
        lambda: fake_today,
    )

    fy = FinancialYear.objects.create(
        society=society,
        name="FY 2025-26",
        start_date=date(2025, 4, 1),
        end_date=date(2026, 3, 31),
        is_open=True,
    )

    open_periods = AccountingPeriod.objects.filter(
        society=society, financial_year=fy, is_open=True,
    ).order_by("start_date")

    # April through August should be open (5 periods).
    assert open_periods.count() == 5
    assert open_periods.first().start_date == date(2025, 4, 1)
    assert open_periods.last().start_date == date(2025, 8, 1)
