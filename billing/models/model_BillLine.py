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

    def __str__(self):
        return f"{self.bill} - {self.description}"
