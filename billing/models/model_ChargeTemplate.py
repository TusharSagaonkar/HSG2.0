from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models


class ChargeTemplate(models.Model):
    class Frequency(models.TextChoices):
        MONTHLY = "MONTHLY", "Monthly"
        QUARTERLY = "QUARTERLY", "Quarterly"
        YEARLY = "YEARLY", "Yearly"

    society = models.ForeignKey(
        "housing.Society",
        on_delete=models.CASCADE,
        related_name="charge_templates",
    )
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    frequency = models.CharField(max_length=20, choices=Frequency.choices)
    due_days = models.PositiveIntegerField(default=15)
    late_fee_percent = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("0.00"))
    income_account = models.ForeignKey(
        "accounting.Account",
        on_delete=models.PROTECT,
        related_name="charge_templates_as_income",
    )
    receivable_account = models.ForeignKey(
        "accounting.Account",
        on_delete=models.PROTECT,
        related_name="charge_templates_as_receivable",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        app_label = "housing"
        ordering = ("name",)
        unique_together = ("society", "name")

    def clean(self):
        if self.income_account and self.income_account.society_id != self.society_id:
            raise ValidationError("Income account must belong to the selected society.")
        if self.receivable_account and self.receivable_account.society_id != self.society_id:
            raise ValidationError("Receivable account must belong to the selected society.")

    def __str__(self):
        return self.name
