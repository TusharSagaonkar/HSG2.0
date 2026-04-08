from decimal import Decimal

import pytest
from django.utils import timezone

from accounting.models import AccountingPeriod
from accounting.models import Voucher
from accounting.services.gst_vouchers import create_expense_with_gst
from accounting.services.gst_vouchers import create_fund_transfer
from accounting.services.gst_vouchers import create_member_advance_adjustment
from accounting.services.gst_vouchers import create_member_advance_receipt
from accounting.services.gst_vouchers import create_maintenance_billing_with_gst
from accounting.services.gst_vouchers import create_member_payment_receipt
from accounting.services.gst_vouchers import create_vendor_payment
from housing.models import Society
from members.models import Structure
from members.models import Unit


pytestmark = pytest.mark.django_db


def _open_current_period(society):
    today = timezone.localdate()
    AccountingPeriod.objects.filter(
        society=society,
        start_date__lte=today,
        end_date__gte=today,
    ).update(is_open=True)


def _make_unit(society, identifier="101"):
    structure = Structure.objects.create(
        society=society,
        structure_type=Structure.StructureType.BUILDING,
        name=f"Tower-{identifier}",
    )
    return Unit.objects.create(
        structure=structure,
        unit_type=Unit.UnitType.FLAT,
        identifier=identifier,
    )


def test_create_maintenance_billing_with_gst_posts_balanced_entries():
    society = Society.objects.create(name="GST Billing Service Society")
    _open_current_period(society)
    unit = _make_unit(society)

    voucher = create_maintenance_billing_with_gst(
        society=society,
        voucher_date=timezone.localdate(),
        base_amount=Decimal("1000.00"),
        gst_rate=Decimal("18.00"),
        unit=unit,
    )

    assert voucher.posted_at is not None
    entries = list(voucher.entries.select_related("account").order_by("id"))
    assert len(entries) == 4
    assert any(entry.account.name == "Output CGST" and entry.credit == Decimal("90.00") for entry in entries)
    assert any(entry.account.name == "Output SGST" and entry.credit == Decimal("90.00") for entry in entries)


def test_create_expense_with_gst_posts_balanced_entries():
    society = Society.objects.create(name="GST Expense Service Society")
    _open_current_period(society)

    voucher = create_expense_with_gst(
        society=society,
        voucher_date=timezone.localdate(),
        expense_amount=Decimal("1000.00"),
        gst_amount=Decimal("180.00"),
        expense_account_name="Electricity Expense",
    )

    assert voucher.posted_at is not None
    assert voucher.voucher_type == Voucher.VoucherType.BILL
    entries = list(voucher.entries.select_related("account").order_by("id"))
    assert any(entry.account.name == "Input CGST" and entry.debit == Decimal("90.00") for entry in entries)
    assert any(entry.account.name == "Input SGST" and entry.debit == Decimal("90.00") for entry in entries)
    assert any(entry.account.name == "Vendor Payable" and entry.credit == Decimal("1180.00") for entry in entries)


def test_create_member_payment_receipt_posts_bank_against_receivable():
    society = Society.objects.create(name="Member Receipt Service Society")
    _open_current_period(society)
    unit = _make_unit(society, identifier="202")

    voucher = create_member_payment_receipt(
        society=society,
        voucher_date=timezone.localdate(),
        amount=Decimal("1180.00"),
        unit=unit,
        reference_number="UTR-2026-01",
    )

    assert voucher.posted_at is not None
    entries = list(voucher.entries.select_related("account").order_by("id"))
    assert any(entry.account.name == "Bank Account – Main" and entry.debit == Decimal("1180.00") for entry in entries)
    assert any(entry.account.name == "Maintenance Receivable" and entry.credit == Decimal("1180.00") for entry in entries)


def test_create_vendor_payment_posts_payable_against_bank():
    society = Society.objects.create(name="Vendor Payment Service Society")
    _open_current_period(society)

    voucher = create_vendor_payment(
        society=society,
        voucher_date=timezone.localdate(),
        amount=Decimal("1180.00"),
        reference_number="VND-1180",
    )

    assert voucher.posted_at is not None
    entries = list(voucher.entries.select_related("account").order_by("id"))
    assert any(entry.account.name == "Vendor Payable" and entry.debit == Decimal("1180.00") for entry in entries)
    assert any(entry.account.name == "Bank Account – Main" and entry.credit == Decimal("1180.00") for entry in entries)


def test_create_fund_transfer_posts_between_bank_accounts():
    society = Society.objects.create(name="Fund Transfer Service Society")
    _open_current_period(society)

    voucher = create_fund_transfer(
        society=society,
        voucher_date=timezone.localdate(),
        amount=Decimal("50000.00"),
        from_bank_account_name="Bank Account – Main",
        to_bank_account_name="Bank Account – Sinking Fund",
    )

    assert voucher.posted_at is not None
    entries = list(voucher.entries.select_related("account").order_by("id"))
    assert any(entry.account.name == "Bank Account – Sinking Fund" and entry.debit == Decimal("50000.00") for entry in entries)
    assert any(entry.account.name == "Bank Account – Main" and entry.credit == Decimal("50000.00") for entry in entries)


def test_member_advance_receipt_and_adjustment_flow():
    society = Society.objects.create(name="Member Advance Service Society")
    _open_current_period(society)
    unit = _make_unit(society, identifier="303")

    receipt = create_member_advance_receipt(
        society=society,
        voucher_date=timezone.localdate(),
        amount=Decimal("5000.00"),
        reference_number="ADV-5000",
    )
    assert receipt.posted_at is not None
    receipt_entries = list(receipt.entries.select_related("account").order_by("id"))
    assert any(entry.account.name == "Bank Account – Main" and entry.debit == Decimal("5000.00") for entry in receipt_entries)
    assert any(entry.account.name == "Member Advance" and entry.credit == Decimal("5000.00") for entry in receipt_entries)

    adjustment = create_member_advance_adjustment(
        society=society,
        voucher_date=timezone.localdate(),
        amount=Decimal("5000.00"),
        unit=unit,
    )
    assert adjustment.posted_at is not None
    adjustment_entries = list(adjustment.entries.select_related("account").order_by("id"))
    assert any(entry.account.name == "Member Advance" and entry.debit == Decimal("5000.00") for entry in adjustment_entries)
    assert any(entry.account.name == "Maintenance Receivable" and entry.credit == Decimal("5000.00") for entry in adjustment_entries)
