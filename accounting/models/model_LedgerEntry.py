from django.db import models
from django.core.exceptions import ValidationError
from decimal import Decimal

from .model_Voucher import Voucher
from .model_Account import Account
from members.models import Unit


class LedgerEntry(models.Model):
    voucher = models.ForeignKey(
        Voucher,
        on_delete=models.CASCADE,
        related_name="entries",
    )

    account = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        related_name="ledger_entries",
    )

    unit = models.ForeignKey(
        Unit,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="ledger_entries",
    )

    debit = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )

    credit = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )

    class Meta:
        ordering = ("id",)
        indexes = [
            models.Index(fields=["account", "voucher"]),
            models.Index(fields=["voucher", "id"]),
        ]

    def clean(self):
        super().clean()

        if self.voucher.posted_at:
            raise ValidationError("Cannot modify entries of a posted voucher.")

        if self.account and not self.account.is_active:
            raise ValidationError({"account": "Cannot post to an inactive account."})

        if self.account and self.voucher and self.account.society_id != self.voucher.society_id:
            raise ValidationError({"account": "Account must belong to the voucher society."})

        if self.unit and self.voucher and self.unit.structure.society_id != self.voucher.society_id:
            raise ValidationError({"unit": "Unit must belong to the voucher society."})

        if self.debit < 0 or self.credit < 0:
            raise ValidationError("Debit and credit amounts must be positive.")

        if self.debit > 0 and self.credit > 0:
            raise ValidationError("Ledger entry cannot have both debit and credit.")

        if self.debit == 0 and self.credit == 0:
            raise ValidationError("Ledger entry must have either debit or credit.")

        account_type = self.account.category.account_type

        if account_type in ("ASSET", "LIABILITY"):
            if self.account.name in (
                "Maintenance Receivable",
                "Advance Receivable",
                "Advance from Members",
                "Security Deposit Payable",
            ) and not self.unit:
                raise ValidationError(
                    {"unit": "Unit is required for receivable/payable accounts."}
                )

        if account_type in ("INCOME", "EXPENSE", "EQUITY") and self.unit:
            raise ValidationError(
                {"unit": "Unit must not be set for income, expense, or equity accounts."}
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.voucher.posted_at:
            raise ValidationError("Cannot delete entries of a posted voucher.")
        super().delete(*args, **kwargs)

    def __str__(self):
        return f"{self.account} D:{self.debit} C:{self.credit}"
