from django.db import models
from django.core.exceptions import ValidationError
from django.conf import settings
from societies.models import Society
from accounting.models.model_LedgerEntry import LedgerEntry


class ReconciliationLink(models.Model):
    """
    The core reconciliation mapping table.

    Creates a many-to-many bridge between accounting LedgerEntries
    and BankTransaction records. Supports:
      - exact matching (one-to-one)
      - partial matching (amount differences)
      - split matching (one-to-many / many-to-one)
      - force matching (manual override)
    """

    class ExceptionType(models.TextChoices):
        BOOK_ONLY = "BOOK_ONLY", "Book Only (missing in bank)"
        BANK_ONLY = "BANK_ONLY", "Bank Only (missing in books)"
        AMOUNT_MISMATCH = "AMOUNT_MISMATCH", "Amount Mismatch"
        DATE_MISMATCH = "DATE_MISMATCH", "Date Mismatch"
        DUPLICATE_BOOK = "DUPLICATE_BOOK", "Duplicate Book Entry"
        DUPLICATE_BANK = "DUPLICATE_BANK", "Duplicate Bank Entry"

    class MatchType(models.TextChoices):
        EXACT = "EXACT", "Exact"
        PARTIAL = "PARTIAL", "Partial"
        SPLIT = "SPLIT", "Split"
        FORCE = "FORCE", "Force"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        SUGGESTED = "SUGGESTED", "Suggested"
        MATCHED = "MATCHED", "Matched"
        PARTIAL = "PARTIAL", "Partial"
        DUPLICATE = "DUPLICATE", "Duplicate"
        EXCEPTION = "EXCEPTION", "Exception"
        REVERSED = "REVERSED", "Reversed"
        FORCE_MATCHED = "FORCE_MATCHED", "Force Matched"
        IGNORED = "IGNORED", "Ignored"

    society = models.ForeignKey(
        Society,
        on_delete=models.CASCADE,
        related_name="reconciliation_links",
    )
    voucher_entry = models.ForeignKey(
        LedgerEntry,
        on_delete=models.PROTECT,
        related_name="reconciliation_links",
        help_text="The accounting ledger entry being reconciled",
    )
    bank_transaction = models.ForeignKey(
        "reconciliation.BankTransaction",
        on_delete=models.PROTECT,
        related_name="reconciliation_links",
        help_text="The bank transaction being matched",
    )
    matched_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="The amount being matched in this link (for partial/split matches)",
    )
    match_type = models.CharField(
        max_length=20,
        choices=MatchType.choices,
        default=MatchType.EXACT,
    )
    confidence_score = models.IntegerField(
        default=0,
        help_text="Match confidence 0-100. Higher values = stronger match.",
    )
    matched_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="reconciliation_matches",
        null=True,
        blank=True,
    )
    matched_at = models.DateTimeField(null=True, blank=True)
    is_manual = models.BooleanField(
        default=False,
        help_text="True if manually matched by a user, False if auto-matched",
    )
    remarks = models.TextField(blank=True, default="")
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    exception_type = models.CharField(
        max_length=20,
        choices=ExceptionType.choices,
        blank=True,
        default="",
        help_text="Categorizes the type of exception for reporting and resolution",
    )

    class Meta:
        ordering = ("-matched_at", "-id")
        indexes = [
            models.Index(fields=["society", "status"]),
            models.Index(fields=["society", "exception_type"]),
            models.Index(fields=["society", "voucher_entry"]),
            models.Index(fields=["society", "bank_transaction"]),
            models.Index(fields=["voucher_entry", "bank_transaction"]),
            models.Index(fields=["status", "matched_at"]),
        ]

    def clean(self):
        if self.voucher_entry.voucher.society != self.society:
            raise ValidationError(
                "Voucher entry must belong to the same society as the reconciliation link."
            )
        if self.voucher_entry.voucher.society != self.bank_transaction.bank_statement_import.society:
            raise ValidationError(
                "Bank transaction must belong to the same society as the voucher."
            )
        if self.matched_amount <= 0:
            raise ValidationError("Matched amount must be positive.")

        if self.confidence_score < 0 or self.confidence_score > 100:
            raise ValidationError("Confidence score must be between 0 and 100.")

        if self.match_type == self.MatchType.EXACT and self.status not in {
            self.Status.MATCHED,
            self.Status.FORCE_MATCHED,
            self.Status.REVERSED,
            self.Status.DUPLICATE,
            self.Status.EXCEPTION,
            self.Status.IGNORED,
        }:
            raise ValidationError(
                "Exact match links must be matched or in a terminal review status."
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def confirm_match(self, user):
        """Confirm a suggested match."""
        if self.status not in {self.Status.SUGGESTED, self.Status.PENDING}:
            raise ValidationError(
                f"Cannot confirm a link with status '{self.status}'."
            )
        self.status = self.Status.MATCHED
        self.is_manual = True
        self.matched_by = user
        from django.utils import timezone
        self.matched_at = timezone.now()
        self.save(update_fields=["status", "is_manual", "matched_by", "matched_at"])

    def unmatch(self, user, reason=""):
        """Reverse a previously matched link."""
        if self.status not in {
            self.Status.MATCHED,
            self.Status.FORCE_MATCHED,
            self.Status.PARTIAL,
        }:
            raise ValidationError(
                f"Cannot unmatch a link with status '{self.status}'."
            )
        self.status = self.Status.REVERSED
        self.remarks = reason or "Unmatched by user."
        self.matched_by = user
        self.save(update_fields=["status", "remarks", "matched_by"])

    def mark_duplicate(self, user):
        """Flag this link as a duplicate."""
        self.status = self.Status.DUPLICATE
        self.matched_by = user
        self.save(update_fields=["status", "matched_by"])

    def mark_exception(self, user, exception_type="", reason=""):
        """Mark this link as needing investigation."""
        self.status = self.Status.EXCEPTION
        self.exception_type = exception_type
        self.remarks = reason
        self.matched_by = user
        self.save(update_fields=["status", "exception_type", "remarks", "matched_by"])

    def __str__(self):
        return (
            f"Link[{self.status}] "
            f"LE#{self.voucher_entry_id} ↔ BT#{self.bank_transaction_id} "
            f"₹{self.matched_amount} ({self.confidence_score}%)"
        )