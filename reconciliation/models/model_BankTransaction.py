import hashlib
from django.db import models
from django.core.exceptions import ValidationError


class BankTransaction(models.Model):
    class DrCr(models.TextChoices):
        DEBIT = "DEBIT", "Debit"
        CREDIT = "CREDIT", "Credit"

    bank_statement_import = models.ForeignKey(
        "reconciliation.BankStatementImport",
        on_delete=models.CASCADE,
        related_name="transactions",
    )
    source_row_index = models.IntegerField(
        null=True,
        blank=True,
        help_text="Row number from source or manual grid",
    )
    transaction_date = models.DateField(db_index=True)
    value_date = models.DateField(
        null=True,
        blank=True,
        help_text="Value date if available from bank statement",
    )
    narration = models.TextField(
        help_text="Raw bank narration — immutable",
    )
    reference_no = models.CharField(
        max_length=120,
        blank=True,
        default="",
        db_index=True,
        help_text="Bank reference / transaction ID",
    )
    cheque_no = models.CharField(
        max_length=30,
        blank=True,
        default="",
    )
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
    )
    dr_cr = models.CharField(
        max_length=6,
        choices=DrCr.choices,
    )
    balance = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Running balance after this transaction",
    )
    raw_row_data = models.JSONField(
        default=dict,
        help_text="Complete original row data preserved for audit",
    )
    duplicate_hash = models.CharField(
        max_length=64,
        blank=True,
        default="",
        db_index=True,
        help_text="Hash of date+amount+narration+reference for internal duplicate detection",
    )
    is_duplicate = models.BooleanField(
        default=False,
        help_text="Flagged as duplicate within this import batch",
    )

    class Meta:
        ordering = ("transaction_date", "id")
        indexes = [
            models.Index(fields=["bank_statement_import", "transaction_date"]),
            models.Index(fields=["reference_no"]),
            models.Index(fields=["cheque_no"]),
            models.Index(fields=["duplicate_hash"]),
            models.Index(fields=["amount", "transaction_date"]),
        ]

    @staticmethod
    def compute_duplicate_hash(transaction_date, amount, narration, reference_no) -> str:
        """Compute a hash for detecting duplicate transactions within statements."""
        raw = f"{transaction_date}|{amount}|{narration.strip().lower()}|{reference_no.strip().lower()}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def clean(self):
        if self.amount <= 0:
            raise ValidationError("Transaction amount must be positive.")

    def save(self, *args, **kwargs):
        # Only allow updates to is_duplicate flag, not the data itself
        if self.pk:
            original = BankTransaction.objects.get(pk=self.pk)
            immutable_fields = {
                "bank_statement_import",
                "transaction_date",
                "value_date",
                "narration",
                "reference_no",
                "cheque_no",
                "amount",
                "dr_cr",
                "balance",
                "raw_row_data",
                "duplicate_hash",
            }
            for field in immutable_fields:
                if getattr(self, field) != getattr(original, field):
                    raise ValidationError(
                        f"BankTransaction.{field} is immutable once created."
                    )
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(
            "Bank transactions cannot be deleted. They are permanent records."
        )

    def __str__(self):
        return f"{self.transaction_date} | {self.dr_cr} ₹{self.amount} | {self.reference_no or self.narration[:60]}"
