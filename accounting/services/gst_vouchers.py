from __future__ import annotations

from decimal import Decimal

from django.db import transaction

from accounting.models import Account
from accounting.models import LedgerEntry
from accounting.models import Voucher


def _account(society, name):
    return Account.objects.get(society=society, name=name)


def _post_voucher(*, society, voucher_date, voucher_type, narration, rows, payment_mode="", reference_number=""):
    with transaction.atomic():
        voucher = Voucher.objects.create(
            society=society,
            voucher_date=voucher_date,
            voucher_type=voucher_type,
            narration=narration,
            payment_mode=payment_mode,
            reference_number=reference_number,
        )
        for row in rows:
            LedgerEntry.objects.create(
                voucher=voucher,
                account=row["account"],
                unit=row.get("unit"),
                debit=row.get("debit", Decimal("0.00")),
                credit=row.get("credit", Decimal("0.00")),
                reference_type=row.get("reference_type", LedgerEntry.ReferenceType.NONE),
                reference_id=row.get("reference_id", ""),
            )
        voucher.post()
        return voucher


def create_maintenance_billing_with_gst(
    *,
    society,
    voucher_date,
    base_amount: Decimal,
    gst_rate: Decimal = Decimal("18.00"),
    receivable_account_name: str = "Maintenance Receivable",
    income_account_name: str = "Maintenance Charges",
    output_cgst_account_name: str = "Output CGST",
    output_sgst_account_name: str = "Output SGST",
    unit=None,
):
    if unit is None:
        raise ValueError("unit is required for maintenance receivable billing entries.")
    cgst = (base_amount * (gst_rate / Decimal("2")) / Decimal("100")).quantize(Decimal("0.01"))
    sgst = cgst
    total = base_amount + cgst + sgst
    return _post_voucher(
        society=society,
        voucher_date=voucher_date,
        voucher_type=Voucher.VoucherType.BILL,
        narration="Maintenance billing with GST split",
        rows=[
            {"account": _account(society, receivable_account_name), "unit": unit, "debit": total},
            {"account": _account(society, income_account_name), "credit": base_amount},
            {"account": _account(society, output_cgst_account_name), "credit": cgst},
            {"account": _account(society, output_sgst_account_name), "credit": sgst},
        ],
    )


def create_expense_with_gst(
    *,
    society,
    voucher_date,
    expense_amount: Decimal,
    gst_amount: Decimal,
    expense_account_name: str,
    input_cgst_account_name: str = "Input CGST",
    input_sgst_account_name: str = "Input SGST",
    vendor_payable_account_name: str = "Vendor Payable",
):
    half_gst = (gst_amount / Decimal("2")).quantize(Decimal("0.01"))
    total = expense_amount + gst_amount
    return _post_voucher(
        society=society,
        voucher_date=voucher_date,
        voucher_type=Voucher.VoucherType.BILL,
        narration="Expense booked with GST split",
        rows=[
            {"account": _account(society, expense_account_name), "debit": expense_amount},
            {"account": _account(society, input_cgst_account_name), "debit": half_gst},
            {"account": _account(society, input_sgst_account_name), "debit": half_gst},
            {"account": _account(society, vendor_payable_account_name), "credit": total},
        ],
    )


def create_member_payment_receipt(
    *,
    society,
    voucher_date,
    amount: Decimal,
    bank_account_name: str = "Bank Account – Main",
    receivable_account_name: str = "Maintenance Receivable",
    unit=None,
    payment_mode: str = Voucher.PaymentMode.BANK_TRANSFER,
    reference_number: str = "",
):
    return _post_voucher(
        society=society,
        voucher_date=voucher_date,
        voucher_type=Voucher.VoucherType.RECEIPT,
        narration="Member receipt against receivable",
        payment_mode=payment_mode,
        reference_number=reference_number,
        rows=[
            {"account": _account(society, bank_account_name), "debit": amount},
            {"account": _account(society, receivable_account_name), "unit": unit, "credit": amount},
        ],
    )


def create_vendor_payment(
    *,
    society,
    voucher_date,
    amount: Decimal,
    bank_account_name: str = "Bank Account – Main",
    vendor_payable_account_name: str = "Vendor Payable",
    payment_mode: str = Voucher.PaymentMode.BANK_TRANSFER,
    reference_number: str = "",
):
    return _post_voucher(
        society=society,
        voucher_date=voucher_date,
        voucher_type=Voucher.VoucherType.PAYMENT,
        narration="Vendor payment against payable",
        payment_mode=payment_mode,
        reference_number=reference_number,
        rows=[
            {"account": _account(society, vendor_payable_account_name), "debit": amount},
            {"account": _account(society, bank_account_name), "credit": amount},
        ],
    )


def create_fund_transfer(
    *,
    society,
    voucher_date,
    amount: Decimal,
    from_bank_account_name: str = "Bank Account – Main",
    to_bank_account_name: str = "Bank Account – Sinking Fund",
    payment_mode: str = Voucher.PaymentMode.BANK_TRANSFER,
    reference_number: str = "",
):
    return _post_voucher(
        society=society,
        voucher_date=voucher_date,
        voucher_type=Voucher.VoucherType.JOURNAL,
        narration="Fund transfer between bank accounts",
        payment_mode=payment_mode,
        reference_number=reference_number,
        rows=[
            {"account": _account(society, to_bank_account_name), "debit": amount},
            {"account": _account(society, from_bank_account_name), "credit": amount},
        ],
    )


def create_member_advance_receipt(
    *,
    society,
    voucher_date,
    amount: Decimal,
    bank_account_name: str = "Bank Account – Main",
    member_advance_account_name: str = "Member Advance",
    payment_mode: str = Voucher.PaymentMode.BANK_TRANSFER,
    reference_number: str = "",
):
    return _post_voucher(
        society=society,
        voucher_date=voucher_date,
        voucher_type=Voucher.VoucherType.RECEIPT,
        narration="Member advance receipt",
        payment_mode=payment_mode,
        reference_number=reference_number,
        rows=[
            {"account": _account(society, bank_account_name), "debit": amount},
            {"account": _account(society, member_advance_account_name), "credit": amount},
        ],
    )


def create_member_advance_adjustment(
    *,
    society,
    voucher_date,
    amount: Decimal,
    unit,
    member_advance_account_name: str = "Member Advance",
    receivable_account_name: str = "Maintenance Receivable",
):
    return _post_voucher(
        society=society,
        voucher_date=voucher_date,
        voucher_type=Voucher.VoucherType.JOURNAL,
        narration="Member advance adjusted against receivable",
        rows=[
            {"account": _account(society, member_advance_account_name), "debit": amount},
            {"account": _account(society, receivable_account_name), "unit": unit, "credit": amount},
        ],
    )
