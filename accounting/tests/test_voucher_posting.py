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
def test_draft_voucher_has_no_number():
    society = Society.objects.create(name="Test Society")

    v = Voucher.objects.create(
        society=society,
        voucher_type="GENERAL",
        voucher_date=date(2024, 8, 6),
    )

    assert v.voucher_number is None
    assert v.posted_at is None


@pytest.mark.django_db
def test_voucher_post_generates_number_and_timestamp():
    society = Society.objects.create(name="Test Society")

    FinancialYear.objects.create(
        name="FY 2024-25",
        start_date=date(2024, 4, 1),
        end_date=date(2025, 3, 31),
        is_open=True,
    )

    AccountingPeriod.objects.create(year=2024, month=8, is_open=True)

    cat = AccountCategory.objects.create(name="Cash", account_type="ASSET")
    acc = Account.objects.create(name="Cash", category=cat)

    v = Voucher.objects.create(
        society=society,
        voucher_type="GENERAL",
        voucher_date=date(2024, 8, 6),
    )

    LedgerEntry.objects.create(voucher=v, account=acc, debit=100)
    LedgerEntry.objects.create(voucher=v, account=acc, credit=100)

    v.post()
    v.refresh_from_db()

    assert v.voucher_number == 1
    assert v.posted_at is not None


@pytest.mark.django_db
def test_posting_twice_is_blocked():
    society = Society.objects.create(name="Test Society")

    FinancialYear.objects.create(
        name="FY 2024-25",
        start_date=date(2024, 4, 1),
        end_date=date(2025, 3, 31),
        is_open=True,
    )

    AccountingPeriod.objects.create(year=2024, month=8, is_open=True)

    cat = AccountCategory.objects.create(name="Cash", account_type="ASSET")
    acc = Account.objects.create(name="Cash", category=cat)

    v = Voucher.objects.create(
        society=society,
        voucher_type="GENERAL",
        voucher_date=date(2024, 8, 6),
    )

    LedgerEntry.objects.create(voucher=v, account=acc, debit=50)
    LedgerEntry.objects.create(voucher=v, account=acc, credit=50)

    v.post()

    with pytest.raises(ValidationError):
        v.post()
