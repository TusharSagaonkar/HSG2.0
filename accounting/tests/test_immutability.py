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
def test_posted_voucher_is_immutable():
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

    LedgerEntry.objects.create(voucher=v, account=acc, debit=10)
    LedgerEntry.objects.create(voucher=v, account=acc, credit=10)

    v.post()

    v.narration = "Tampered"
    with pytest.raises(ValidationError):
        v.save()


@pytest.mark.django_db
def test_ledger_entry_blocked_after_posting():
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

    e1 = LedgerEntry.objects.create(voucher=v, account=acc, debit=20)
    LedgerEntry.objects.create(voucher=v, account=acc, credit=20)

    v.post()

    e1.debit = 30
    with pytest.raises(ValidationError):
        e1.save()
