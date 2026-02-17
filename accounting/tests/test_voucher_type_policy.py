from datetime import date

import pytest
from django.core.exceptions import ValidationError

from accounting.models import Account
from accounting.models import AccountCategory
from accounting.models import AccountingPeriod
from accounting.models import FinancialYear
from accounting.models import LedgerEntry
from accounting.models import Voucher
from housing.models import Society


@pytest.mark.django_db
def test_receipt_voucher_requires_payment_mode_and_reference():
    society = Society.objects.create(name="Receipt Society")
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
    ).update(is_open=True)

    bank_cat, _ = AccountCategory.objects.get_or_create(
        society=society,
        name="Bank & Cash",
        account_type="ASSET",
    )
    income_cat, _ = AccountCategory.objects.get_or_create(
        society=society,
        name="Maintenance Income",
        account_type="INCOME",
    )
    bank, _ = Account.objects.get_or_create(
        society=society,
        name="Bank Account – Main",
        category=bank_cat,
    )
    income, _ = Account.objects.get_or_create(
        society=society,
        name="Maintenance Charges",
        category=income_cat,
    )

    voucher = Voucher.objects.create(
        society=society,
        voucher_type=Voucher.VoucherType.RECEIPT,
        voucher_date=date(2024, 8, 6),
        narration="Member receipt",
    )
    LedgerEntry.objects.create(voucher=voucher, account=bank, debit=1000)
    LedgerEntry.objects.create(voucher=voucher, account=income, credit=1000)

    with pytest.raises(ValidationError):
        voucher.post()

    voucher.payment_mode = Voucher.PaymentMode.UPI
    with pytest.raises(ValidationError):
        voucher.post()

    voucher.reference_number = "UTR-001"
    voucher.post()
    assert voucher.posted_at is not None


@pytest.mark.django_db
def test_receipt_voucher_requires_cash_or_bank_debit():
    society = Society.objects.create(name="Receipt Rule Society")
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
    ).update(is_open=True)

    asset_cat, _ = AccountCategory.objects.get_or_create(
        society=society,
        name="Current Assets",
        account_type="ASSET",
    )
    income_cat, _ = AccountCategory.objects.get_or_create(
        society=society,
        name="Maintenance Income",
        account_type="INCOME",
    )
    non_cash_asset, _ = Account.objects.get_or_create(
        society=society,
        name="Prepaid Insurance",
        category=asset_cat,
    )
    income, _ = Account.objects.get_or_create(
        society=society,
        name="Maintenance Charges",
        category=income_cat,
    )

    voucher = Voucher.objects.create(
        society=society,
        voucher_type=Voucher.VoucherType.RECEIPT,
        voucher_date=date(2024, 8, 6),
        payment_mode=Voucher.PaymentMode.CASH,
        narration="Receipt without cash/bank leg",
    )
    LedgerEntry.objects.create(voucher=voucher, account=non_cash_asset, debit=1000)
    LedgerEntry.objects.create(voucher=voucher, account=income, credit=1000)

    with pytest.raises(ValidationError):
        voucher.post()


@pytest.mark.django_db
def test_payment_voucher_requires_cash_or_bank_credit():
    society = Society.objects.create(name="Payment Rule Society")
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
    ).update(is_open=True)

    expense_cat, _ = AccountCategory.objects.get_or_create(
        society=society,
        name="Utility Expenses",
        account_type="EXPENSE",
    )
    payable_cat, _ = AccountCategory.objects.get_or_create(
        society=society,
        name="Vendor Payables",
        account_type="LIABILITY",
    )
    expense, _ = Account.objects.get_or_create(
        society=society,
        name="Electricity Expense",
        category=expense_cat,
    )
    payable, _ = Account.objects.get_or_create(
        society=society,
        name="Vendor Payable",
        category=payable_cat,
    )

    voucher = Voucher.objects.create(
        society=society,
        voucher_type=Voucher.VoucherType.PAYMENT,
        voucher_date=date(2024, 8, 6),
        payment_mode=Voucher.PaymentMode.CASH,
        narration="Payment without cash/bank credit",
    )
    LedgerEntry.objects.create(voucher=voucher, account=expense, debit=500)
    LedgerEntry.objects.create(voucher=voucher, account=payable, credit=500)

    with pytest.raises(ValidationError):
        voucher.post()
