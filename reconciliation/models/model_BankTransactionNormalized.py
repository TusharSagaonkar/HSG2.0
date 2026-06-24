from django.db import models


class BankTransactionNormalized(models.Model):
    """
    Normalized and cleaned version of a BankTransaction used for matching.

    Raw bank data remains immutable in BankTransaction.
    This model holds extracted/cleaned fields for the matching engine.
    """
    bank_transaction = models.OneToOneField(
        "reconciliation.BankTransaction",
        on_delete=models.CASCADE,
        related_name="normalized",
    )
    cleaned_narration = models.TextField(
        blank=True,
        default="",
        help_text="Cleaned and normalized narration text",
    )
    extracted_utr = models.CharField(
        max_length=64,
        blank=True,
        default="",
        db_index=True,
        help_text="Extracted UTR / transaction reference number",
    )
    extracted_flat_no = models.CharField(
        max_length=20,
        blank=True,
        default="",
        help_text="Extracted flat/unit number from narration",
    )
    extracted_reference = models.CharField(
        max_length=120,
        blank=True,
        default="",
        help_text="Extracted reference from narration",
    )
    extracted_amount_words = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Extracted amount in words from narration",
    )
    normalized_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["extracted_utr"]),
            models.Index(fields=["extracted_flat_no"]),
            models.Index(fields=["extracted_reference"]),
        ]

    def __str__(self):
        return f"Normalized: {self.bank_transaction}"