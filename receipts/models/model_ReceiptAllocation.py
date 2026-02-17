from django.core.exceptions import ValidationError
from django.db import models


class ReceiptAllocation(models.Model):
    receipt = models.ForeignKey(
        "housing.PaymentReceipt",
        on_delete=models.CASCADE,
        related_name="allocations",
    )
    bill = models.ForeignKey(
        "housing.Bill",
        on_delete=models.PROTECT,
        related_name="receipt_allocations",
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        app_label = "housing"
        ordering = ("id",)

    def clean(self):
        if self.bill and self.bill.society_id != self.receipt.society_id:
            raise ValidationError("Allocated bill must belong to same society as receipt.")
        if self.amount <= 0:
            raise ValidationError("Allocation amount must be positive.")

    def __str__(self):
        return f"{self.receipt} -> {self.bill} ({self.amount})"
