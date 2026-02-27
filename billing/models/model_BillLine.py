from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models


class BillLine(models.Model):
    bill = models.ForeignKey(
        "housing.Bill",
        on_delete=models.CASCADE,
        related_name="lines",
    )
    charge_template = models.ForeignKey(
        "housing.ChargeTemplate",
        on_delete=models.PROTECT,
        related_name="bill_lines",
        null=True,
        blank=True,
    )
    description = models.CharField(max_length=255)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    charge_type_snapshot = models.CharField(max_length=20, blank=True)
    rate_snapshot = models.DecimalField(max_digits=12, decimal_places=4, default=Decimal("0.0000"))
    quantity_snapshot = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=Decimal("1.0000"),
    )
    late_fee_percent_snapshot = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    template_version_snapshot = models.PositiveIntegerField(blank=True, null=True)
    calculation_basis = models.CharField(max_length=255, blank=True)
    income_account = models.ForeignKey(
        "accounting.Account",
        on_delete=models.PROTECT,
        related_name="bill_lines",
    )

    class Meta:
        app_label = "housing"
        ordering = ("id",)

    def clean(self):
        if self.income_account and self.income_account.society_id != self.bill.society_id:
            raise ValidationError("Bill line income account must belong to bill society.")
        if self.charge_template and self.charge_template.society_id != self.bill.society_id:
            raise ValidationError("Charge template must belong to bill society.")
        if self.quantity_snapshot <= 0:
            raise ValidationError("Quantity snapshot must be greater than zero.")

    def __str__(self):
        return f"{self.bill} - {self.description}"
