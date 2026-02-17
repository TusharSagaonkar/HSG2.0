from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Sum
from django.utils import timezone


class Bill(models.Model):
    class BillStatus(models.TextChoices):
        OPEN = "OPEN", "Open"
        PARTIAL = "PARTIAL", "Partially Paid"
        PAID = "PAID", "Paid"
        OVERDUE = "OVERDUE", "Overdue"

    society = models.ForeignKey(
        "housing.Society",
        on_delete=models.CASCADE,
        related_name="bills",
    )
    member = models.ForeignKey(
        "housing.Member",
        on_delete=models.PROTECT,
        related_name="bills",
    )
    unit = models.ForeignKey(
        "housing.Unit",
        on_delete=models.PROTECT,
        related_name="bills",
    )
    receivable_account = models.ForeignKey(
        "accounting.Account",
        on_delete=models.PROTECT,
        related_name="bills",
    )
    bill_number = models.PositiveIntegerField()
    bill_period_start = models.DateField()
    bill_period_end = models.DateField()
    bill_date = models.DateField()
    due_date = models.DateField()
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    penalty_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    status = models.CharField(max_length=20, choices=BillStatus.choices, default=BillStatus.OPEN)
    voucher = models.ForeignKey(
        "accounting.Voucher",
        on_delete=models.PROTECT,
        related_name="bills",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "housing"
        ordering = ("-bill_date", "-id")
        unique_together = (
            ("society", "bill_number"),
            ("society", "member", "bill_period_start", "bill_period_end"),
        )

    def clean(self):
        if self.member and self.member.society_id != self.society_id:
            raise ValidationError("Bill member must belong to the selected society.")
        if self.unit and self.unit.structure.society_id != self.society_id:
            raise ValidationError("Bill unit must belong to the selected society.")
        if self.receivable_account and self.receivable_account.society_id != self.society_id:
            raise ValidationError("Bill receivable account must belong to the selected society.")
        if self.bill_period_end < self.bill_period_start:
            raise ValidationError("Bill period end date cannot be before period start date.")
        if self.due_date < self.bill_date:
            raise ValidationError("Bill due date cannot be before bill date.")

    @property
    def allocated_amount(self):
        value = self.receipt_allocations.aggregate(total=Sum("amount"))["total"]
        return value or Decimal("0.00")

    @property
    def outstanding_amount(self):
        value = self.total_amount - self.allocated_amount
        return value if value > 0 else Decimal("0.00")

    def refresh_status(self, as_of_date=None):
        as_of_date = as_of_date or timezone.localdate()
        outstanding = self.outstanding_amount
        if outstanding == 0:
            self.status = self.BillStatus.PAID
        elif outstanding < self.total_amount:
            self.status = self.BillStatus.PARTIAL
        elif self.due_date < as_of_date:
            self.status = self.BillStatus.OVERDUE
        else:
            self.status = self.BillStatus.OPEN
        self.save(update_fields=["status"])

    def __str__(self):
        return f"Bill #{self.bill_number} - {self.member.full_name}"
