from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from accounting.models import LedgerEntry
from accounting.models import Voucher
from receipts.models import PaymentReceipt
from receipts.models import ReceiptAllocation


def post_receipt_for_bill(
    *,
    society,
    member,
    bill,
    amount,
    receipt_date,
    payment_mode,
    deposited_account,
    reference_number="",
):
    if bill.society_id != society.id:
        raise ValidationError("Bill must belong to selected society.")
    if bill.member_id != member.id:
        raise ValidationError("Bill must belong to selected member.")
    if deposited_account.society_id != society.id:
        raise ValidationError("Deposited account must belong to selected society.")
    if payment_mode != "CASH" and not (reference_number or "").strip():
        raise ValidationError("Reference number is required for non-cash receipts.")
    if amount <= 0:
        raise ValidationError("Receipt amount must be positive.")
    if amount > bill.outstanding_amount:
        raise ValidationError("Receipt amount cannot exceed bill outstanding amount.")

    with transaction.atomic():
        receipt = PaymentReceipt(
            society=society,
            member=member,
            unit=bill.unit,
            receipt_date=receipt_date,
            amount=amount,
            payment_mode=payment_mode,
            reference_number=reference_number,
            deposited_account=deposited_account,
        )
        receipt.full_clean()
        receipt.save()
        allocation = ReceiptAllocation(
            receipt=receipt,
            bill=bill,
            amount=amount,
        )
        allocation.full_clean()
        allocation.save()

        voucher = Voucher.objects.create(
            society=society,
            voucher_type=Voucher.VoucherType.RECEIPT,
            voucher_date=receipt_date,
            payment_mode=payment_mode,
            reference_number=reference_number,
            narration=f"Receipt for bill #{bill.bill_number} - {member.full_name}",
        )
        LedgerEntry.objects.create(
            voucher=voucher,
            account=deposited_account,
            debit=amount,
            credit=Decimal("0.00"),
        )
        LedgerEntry.objects.create(
            voucher=voucher,
            account=bill.receivable_account,
            unit=bill.unit,
            debit=Decimal("0.00"),
            credit=amount,
        )
        voucher.post()

        receipt.voucher = voucher
        receipt.save(update_fields=["voucher"])
        bill.refresh_status()
    return receipt
