from django.db import models
from django.core.exceptions import ValidationError
from django.utils import timezone
from decimal import Decimal

class AccountCategory(models.Model):
    class AccountType(models.TextChoices):
        ASSET = "ASSET", "Asset"
        LIABILITY = "LIABILITY", "Liability"
        INCOME = "INCOME", "Income"
        EXPENSE = "EXPENSE", "Expense"
        EQUITY = "EQUITY", "Equity"

    name = models.CharField(max_length=100)
    account_type = models.CharField(
        max_length=20,
        choices=AccountType.choices,
    )

    class Meta:
        unique_together = ("name", "account_type")
        ordering = ("account_type", "name")

    def __str__(self):
        return f"{self.name} ({self.account_type})"
