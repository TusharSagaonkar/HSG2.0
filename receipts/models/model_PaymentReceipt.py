from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class PaymentReceipt(models.Model):
    class ReceiptStatus(models.TextChoices):
        POSTED = "POSTED", "Posted"
        VOID = "VOID", "Void"

    society = models.ForeignKey(
        "housing.Society",
        on_delete=models.CASCADE,
        related_name="payment_receipts",
    )
    member = models.ForeignKey(
        "housing.Member",
        on_delete=models.PROTECT,
        related_name="receipts",
    )
    unit = models.ForeignKey(
        "housing.Unit",
        on_delete=models.PROTECT,
        related_name="receipts",
    )
    receipt_date = models.DateField(default=timezone.localdate)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    payment_mode = models.CharField(
        max_length=20,
        choices=(
            ("CASH", "Cash"),
            ("BANK_TRANSFER", "Bank Transfer"),
            ("CHEQUE", "Cheque"),
            ("UPI", "UPI"),
            ("CARD", "Card"),
            ("OTHER", "Other"),
        ),
    )
    reference_number = models.CharField(max_length=120, blank=True)
    deposited_account = models.ForeignKey(
        "accounting.Account",
        on_delete=models.PROTECT,
        related_name="receipts_as_deposit",
    )
    voucher = models.ForeignKey(
        "accounting.Voucher",
        on_delete=models.PROTECT,
        related_name="payment_receipts",
        null=True,
        blank=True,
    )
    status = models.CharField(max_length=20, choices=ReceiptStatus.choices, default=ReceiptStatus.POSTED)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "housing"
        ordering = ("-receipt_date", "-id")

    def clean(self):
        if self.member and self.member.society_id != self.society_id:
            raise ValidationError("Receipt member must belong to selected society.")
        if self.unit and self.unit.structure.society_id != self.society_id:
            raise ValidationError("Receipt unit must belong to selected society.")
        if self.deposited_account and self.deposited_account.society_id != self.society_id:
            raise ValidationError("Deposited account must belong to selected society.")
        if self.payment_mode != "CASH" and not (self.reference_number or "").strip():
            raise ValidationError("Reference number is required for non-cash receipts.")

    def __str__(self):
        return f"Receipt {self.id} - {self.member.full_name}"
