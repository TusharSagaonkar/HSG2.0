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
