from datetime import date
import pytest
from django.core.exceptions import ValidationError

from accounting.models.model_Voucher import Voucher
from accounting.models.model_LedgerEntry import LedgerEntry
from accounting.models.model_Account import Account
from accounting.models.model_AccountCategory import AccountCategory
from housing.models import Society


@pytest.mark.django_db
def test_same_account_dr_cr_blocked():
    society = Society.objects.create(name="Test Society")

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

    with pytest.raises(ValidationError):
        LedgerEntry.objects.create(
            voucher=v,
            account=acc,
            debit=100,
            credit=100,
        )
