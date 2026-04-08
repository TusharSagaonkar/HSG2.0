from datetime import date
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError

from accounting.models import Account
from accounting.models import AccountCategory
from accounting.models import AccountingPeriod
from accounting.models import FinancialYear
from accounting.models import LedgerEntry
from accounting.models import Voucher
from housing.models import Society
from members.models import Structure
from members.models import Unit


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


@pytest.mark.django_db
def test_gst_policy_blocks_direct_gst_payable_posting():
    society = Society.objects.create(name="GST Direct Payable Society")
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
    liability_cat, _ = AccountCategory.objects.get_or_create(
        society=society,
        name="Statutory Liabilities",
        account_type="LIABILITY",
    )
    receivable, _ = Account.objects.get_or_create(
        society=society,
        name="Maintenance Receivable",
        category=asset_cat,
        defaults={"account_type": "ASSET"},
    )
    structure = Structure.objects.create(
        society=society,
        structure_type=Structure.StructureType.BUILDING,
        name="GST Tower",
    )
    unit = Unit.objects.create(
        structure=structure,
        unit_type=Unit.UnitType.FLAT,
        identifier="101",
    )
    gst_payable, _ = Account.objects.get_or_create(
        society=society,
        name="GST Payable",
        category=liability_cat,
        defaults={"account_type": "LIABILITY", "is_gst": True, "gst_type": "OUTPUT"},
    )

    voucher = Voucher.objects.create(
        society=society,
        voucher_type=Voucher.VoucherType.JOURNAL,
        voucher_date=date(2024, 8, 6),
        narration="Legacy GST payable posting",
    )
    LedgerEntry.objects.create(voucher=voucher, account=receivable, unit=unit, debit=Decimal("100.00"))
    LedgerEntry.objects.create(voucher=voucher, account=gst_payable, credit=Decimal("100.00"))

    with pytest.raises(ValidationError):
        voucher.post()


@pytest.mark.django_db
def test_gst_policy_enforces_input_output_direction():
    society = Society.objects.create(name="GST Direction Society")
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

    liability_cat, _ = AccountCategory.objects.get_or_create(
        society=society,
        name="Statutory Liabilities",
        account_type="LIABILITY",
    )
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
    output_cgst, _ = Account.objects.get_or_create(
        society=society,
        name="Output CGST",
        category=liability_cat,
        defaults={"account_type": "LIABILITY", "is_gst": True, "gst_type": "OUTPUT"},
    )
    maintenance_income, _ = Account.objects.get_or_create(
        society=society,
        name="Maintenance Charges",
        category=income_cat,
        defaults={"account_type": "INCOME"},
    )
    receivable, _ = Account.objects.get_or_create(
        society=society,
        name="Maintenance Receivable",
        category=asset_cat,
        defaults={"account_type": "ASSET"},
    )
    structure = Structure.objects.create(
        society=society,
        structure_type=Structure.StructureType.BUILDING,
        name="GST Direction Tower",
    )
    unit = Unit.objects.create(
        structure=structure,
        unit_type=Unit.UnitType.FLAT,
        identifier="102",
    )

    voucher = Voucher.objects.create(
        society=society,
        voucher_type=Voucher.VoucherType.BILL,
        voucher_date=date(2024, 8, 6),
        narration="Invalid output GST direction",
    )
    LedgerEntry.objects.create(voucher=voucher, account=output_cgst, debit=Decimal("90.00"))
    LedgerEntry.objects.create(voucher=voucher, account=maintenance_income, credit=Decimal("90.00"))

    with pytest.raises(ValidationError):
        voucher.post()

    voucher_2 = Voucher.objects.create(
        society=society,
        voucher_type=Voucher.VoucherType.BILL,
        voucher_date=date(2024, 8, 7),
        narration="Valid GST split direction",
    )
    LedgerEntry.objects.create(voucher=voucher_2, account=receivable, unit=unit, debit=Decimal("1180.00"))
    LedgerEntry.objects.create(voucher=voucher_2, account=maintenance_income, credit=Decimal("1000.00"))
    LedgerEntry.objects.create(voucher=voucher_2, account=output_cgst, credit=Decimal("180.00"))

    voucher_2.post()
    assert voucher_2.posted_at is not None


@pytest.mark.django_db
def test_gst_policy_requires_taxable_base_line():
    society = Society.objects.create(name="GST Base Line Society")
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
        name="Bank & Cash",
        account_type="ASSET",
    )
    liability_cat, _ = AccountCategory.objects.get_or_create(
        society=society,
        name="Statutory Liabilities",
        account_type="LIABILITY",
    )
    bank, _ = Account.objects.get_or_create(
        society=society,
        name="Bank Account – Main",
        category=asset_cat,
        defaults={"account_type": "ASSET"},
    )
    output_cgst, _ = Account.objects.get_or_create(
        society=society,
        name="Output CGST",
        category=liability_cat,
        defaults={"account_type": "LIABILITY", "is_gst": True, "gst_type": "OUTPUT"},
    )

    voucher = Voucher.objects.create(
        society=society,
        voucher_type=Voucher.VoucherType.JOURNAL,
        voucher_date=date(2024, 8, 6),
        narration="GST-only posting without taxable base",
    )
    LedgerEntry.objects.create(voucher=voucher, account=bank, debit=Decimal("90.00"))
    LedgerEntry.objects.create(voucher=voucher, account=output_cgst, credit=Decimal("90.00"))

    with pytest.raises(ValidationError):
        voucher.post()


@pytest.mark.django_db
def test_account_clean_blocks_gst_as_income_expense():
    society = Society.objects.create(name="GST Account Type Society")
    income_cat, _ = AccountCategory.objects.get_or_create(
        society=society,
        name="Income",
        account_type="INCOME",
    )
    invalid_gst_income = Account(
        society=society,
        name="Invalid GST Income",
        category=income_cat,
        account_type="INCOME",
        is_gst=True,
        gst_type="OUTPUT",
    )
    with pytest.raises(ValidationError):
        invalid_gst_income.full_clean()
