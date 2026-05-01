from __future__ import annotations

from decimal import Decimal

from django.db import transaction

from accounting.models import Account
from accounting.models import LedgerEntry
from accounting.models import Voucher


# Account code constants for reliable lookups
class AccountCodes:
    """Account codes from the standard account tree."""
    # Assets - Receivables
    MAINTENANCE_DUE = "1.5.1.1"
    PARKING_DUE = "1.5.1.2"
    INTEREST_ON_ARREARS = "1.5.1.3"
    OTHER_MEMBER_DUES = "1.5.1.4"
    VENDOR_RECEIVABLE = "1.5.2"
    INTEREST_RECEIVABLE_BANK = "1.5.3"

    # Assets - Bank & Cash
    CASH_IN_HAND = "1.4.1"
    BANK_MAINTENANCE = "1.4.2.1"
    BANK_SINKING_FUND = "1.4.2.2"
    BANK_REPAIR_FUND = "1.4.2.3"
    BANK_PARKING_FUND = "1.4.2.4"
    FUND_TRANSFER = "1.4.3"

    # Assets - GST Input
    INPUT_CGST = "1.7.1"
    INPUT_SGST = "1.7.2"
    INPUT_IGST = "1.7.3"

    # Assets - Other
    VENDOR_ADVANCE = "1.6.1"
    STAFF_ADVANCE = "1.6.2"
    PREPAID_EXPENSES = "1.6.3"

    # Liabilities - Member
    ADVANCE_MAINTENANCE = "2.1.1"
    MEMBER_ADVANCE = "2.1.2"
    MEMBER_REFUND_PAYABLE = "2.1.3"
    SECURITY_DEPOSIT_MEMBERS = "2.1.4"

    # Liabilities - Vendor & Expense Payables
    VENDOR_PAYABLE = "2.2.1"
    EXPENSE_PAYABLE = "2.2.2"
    AUDIT_FEES_PAYABLE = "2.2.3"

    # Liabilities - GST Payable
    OUTPUT_CGST = "2.3.1.1"
    OUTPUT_SGST = "2.3.1.2"
    OUTPUT_IGST = "2.3.1.3"
    TDS_PAYABLE = "2.3.2"
    PROFESSIONAL_TAX_PAYABLE = "2.3.3"

    # Liabilities - Bank & Clearing
    CHEQUE_ISSUED_NOT_CLEARED = "2.4.1"
    CHEQUE_DEPOSITED_NOT_CLEARED = "2.4.2"
    PAYMENT_GATEWAY_CLEARING = "2.4.3"

    # Income - Member Income
    MAINTENANCE_CHARGES = "3.1.1"
    SERVICE_CHARGES = "3.1.2"
    PARKING_CHARGES = "3.1.3"
    TRANSFER_FEES = "3.1.4"
    NON_OCCUPANCY_CHARGES = "3.1.5"
    LATE_PAYMENT_PENALTY = "3.1.6"
    INTEREST_INCOME_MEMBER = "3.1.7"

    # Income - Financial
    INTEREST_INCOME_BANK = "3.2.1"
    INTEREST_ON_FD = "3.2.2"

    # Income - Commercial
    RENTAL_INCOME_COMMON_AREA = "3.3.1"
    ADVERTISEMENT_INCOME = "3.3.2"
    MOBILE_TOWER_INCOME = "3.3.3"

    # Expenses
    AUDIT_FEES = "4.1.1"
    PRINTING_STATIONERY = "4.1.2"
    SOFTWARE_EXPENSE = "4.1.3"
    LEGAL_FEES = "4.1.4"
    OFFICE_EXPENSES = "4.1.5"

    # Equity
    SINKING_FUND = "5.1.1"
    REPAIR_MAINTENANCE_FUND = "5.1.2"
    PARKING_FUND = "5.1.3"
    SHARE_CAPITAL = "5.2.1"
    GENERAL_RESERVE = "5.2.2"
    OPENING_BALANCE_FUND = "5.2.3"
    SURPLUS_DEFICIT = "5.3.1"


def _get_account_by_code(society, code: str) -> Account:
    """Get account by code for a society."""
    return Account.objects.get(society=society, code=code)


def _get_account_by_name(society, name: str) -> Account:
    """Get account by name for a society (fallback)."""
    return Account.objects.get(society=society, name=name)


def _account(society, code_or_name):
    """
    Get account by code (preferred) or name (fallback).
    Tries code first, then falls back to name for backward compatibility.
    """
    if isinstance(code_or_name, str) and code_or_name.replace(".", "").isdigit():
        # Looks like a code (digits and dots)
        return _get_account_by_code(society, code_or_name)
    return _get_account_by_name(society, code_or_name)


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
    receivable_account_code: str = AccountCodes.MAINTENANCE_DUE,
    income_account_code: str = AccountCodes.MAINTENANCE_CHARGES,
    output_cgst_account_code: str = AccountCodes.OUTPUT_CGST,
    output_sgst_account_code: str = AccountCodes.OUTPUT_SGST,
    unit=None,
):
    """
    Create a maintenance billing voucher with GST split.
    Posts to:
    - Debit: Maintenance Due (or specified receivable account)
    - Credit: Maintenance Charges (or specified income account)
    - Credit: Output CGST
    - Credit: Output SGST
    """
    if unit is None:
        raise ValueError("unit is required for maintenance receivable billing entries.")

    cgst = ((base_amount * (gst_rate / Decimal("2")) / Decimal("100")).quantize(Decimal("0.01")))
    sgst = cgst
    total = base_amount + cgst + sgst

    return _post_voucher(
        society=society,
        voucher_date=voucher_date,
        voucher_type=Voucher.VoucherType.BILL,
        narration="Maintenance billing with GST split",
        rows=[
            {"account": _account(society, receivable_account_code), "unit": unit, "debit": total},
            {"account": _account(society, income_account_code), "credit": base_amount},
            {"account": _account(society, output_cgst_account_code), "credit": cgst},
            {"account": _account(society, output_sgst_account_code), "credit": sgst},
        ],
    )


def create_expense_with_gst(
    *,
    society,
    voucher_date,
    expense_amount: Decimal,
    gst_amount: Decimal,
    expense_account_code: str,
    input_cgst_account_code: str = AccountCodes.INPUT_CGST,
    input_sgst_account_code: str = AccountCodes.INPUT_SGST,
    vendor_payable_account_code: str = AccountCodes.VENDOR_PAYABLE,
):
    """
    Create an expense voucher with GST input.
    Posts to:
    - Debit: Expense account
    - Debit: Input CGST
    - Debit: Input SGST
    - Credit: Vendor Payable
    """
    half_gst = (gst_amount / Decimal("2")).quantize(Decimal("0.01"))
    total = expense_amount + gst_amount
    return _post_voucher(
        society=society,
        voucher_date=voucher_date,
        voucher_type=Voucher.VoucherType.BILL,
        narration="Expense booked with GST split",
        rows=[
            {"account": _account(society, expense_account_code), "debit": expense_amount},
            {"account": _account(society, input_cgst_account_code), "debit": half_gst},
            {"account": _account(society, input_sgst_account_code), "debit": half_gst},
            {"account": _account(society, vendor_payable_account_code), "credit": total},
        ],
    )


def create_member_payment_receipt(
    *,
    society,
    voucher_date,
    amount: Decimal,
    bank_account_code: str = AccountCodes.BANK_MAINTENANCE,
    receivable_account_code: str = AccountCodes.MAINTENANCE_DUE,
    unit=None,
    payment_mode: str = Voucher.PaymentMode.BANK_TRANSFER,
    reference_number: str = "",
):
    """
    Create a member payment receipt.
    Posts to:
    - Debit: Bank Account
    - Credit: Maintenance Due (or specified receivable account)
    """
    return _post_voucher(
        society=society,
        voucher_date=voucher_date,
        voucher_type=Voucher.VoucherType.RECEIPT,
        narration="Member receipt against receivable",
        payment_mode=payment_mode,
        reference_number=reference_number,
        rows=[
            {"account": _account(society, bank_account_code), "debit": amount},
            {"account": _account(society, receivable_account_code), "unit": unit, "credit": amount},
        ],
    )


def create_vendor_payment(
    *,
    society,
    voucher_date,
    amount: Decimal,
    bank_account_code: str = AccountCodes.BANK_MAINTENANCE,
    vendor_payable_account_code: str = AccountCodes.VENDOR_PAYABLE,
    payment_mode: str = Voucher.PaymentMode.BANK_TRANSFER,
    reference_number: str = "",
):
    """
    Create a vendor payment.
    Posts to:
    - Debit: Vendor Payable
    - Credit: Bank Account
    """
    return _post_voucher(
        society=society,
        voucher_date=voucher_date,
        voucher_type=Voucher.VoucherType.PAYMENT,
        narration="Vendor payment against payable",
        payment_mode=payment_mode,
        reference_number=reference_number,
        rows=[
            {"account": _account(society, vendor_payable_account_code), "debit": amount},
            {"account": _account(society, bank_account_code), "credit": amount},
        ],
    )


def create_fund_transfer(
    *,
    society,
    voucher_date,
    amount: Decimal,
    from_bank_account_code: str = AccountCodes.BANK_MAINTENANCE,
    to_bank_account_code: str = AccountCodes.BANK_SINKING_FUND,
    payment_mode: str = Voucher.PaymentMode.BANK_TRANSFER,
    reference_number: str = "",
):
    """
    Create a fund transfer between bank accounts.
    Posts to:
    - Debit: To Bank Account
    - Credit: From Bank Account
    """
    return _post_voucher(
        society=society,
        voucher_date=voucher_date,
        voucher_type=Voucher.VoucherType.JOURNAL,
        narration="Fund transfer between bank accounts",
        payment_mode=payment_mode,
        reference_number=reference_number,
        rows=[
            {"account": _account(society, to_bank_account_code), "debit": amount},
            {"account": _account(society, from_bank_account_code), "credit": amount},
        ],
    )


def create_member_advance_receipt(
    *,
    society,
    voucher_date,
    amount: Decimal,
    bank_account_code: str = AccountCodes.BANK_MAINTENANCE,
    member_advance_account_code: str = AccountCodes.MEMBER_ADVANCE,
    payment_mode: str = Voucher.PaymentMode.BANK_TRANSFER,
    reference_number: str = "",
):
    """
    Create a member advance receipt.
    Posts to:
    - Debit: Bank Account
    - Credit: Member Advance
    """
    return _post_voucher(
        society=society,
        voucher_date=voucher_date,
        voucher_type=Voucher.VoucherType.RECEIPT,
        narration="Member advance receipt",
        payment_mode=payment_mode,
        reference_number=reference_number,
        rows=[
            {"account": _account(society, bank_account_code), "debit": amount},
            {"account": _account(society, member_advance_account_code), "credit": amount},
        ],
    )


def create_member_advance_adjustment(
    *,
    society,
    voucher_date,
    amount: Decimal,
    unit,
    member_advance_account_code: str = AccountCodes.MEMBER_ADVANCE,
    receivable_account_code: str = AccountCodes.MAINTENANCE_DUE,
):
    """
    Create a member advance adjustment against receivable.
    Posts to:
    - Debit: Member Advance
    - Credit: Maintenance Due (or specified receivable account)
    """
    return _post_voucher(
        society=society,
        voucher_date=voucher_date,
        voucher_type=Voucher.VoucherType.JOURNAL,
        narration="Member advance adjusted against receivable",
        rows=[
            {"account": _account(society, member_advance_account_code), "debit": amount},
            {"account": _account(society, receivable_account_code), "unit": unit, "credit": amount},
        ],
    )
