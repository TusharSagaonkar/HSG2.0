import hashlib
from django.db import models
from django.core.exceptions import ValidationError
from django.conf import settings
from societies.models import Society
from accounting.models.model_Account import Account


class BankStatementImport(models.Model):
    class ImportStatus(models.TextChoices):
        PENDING = "PENDING", "Pending"
        PROCESSING = "PROCESSING", "Processing"
        COMPLETED = "COMPLETED", "Completed"
        FAILED = "FAILED", "Failed"

    society = models.ForeignKey(
        Society,
        on_delete=models.CASCADE,
        related_name="bank_statement_imports",
    )
    bank_account = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        related_name="bank_statement_imports",
        limit_choices_to={"is_bank": True},
        help_text="The bank account this statement belongs to",
    )
    file_name = models.CharField(max_length=255)
    file_hash = models.CharField(
        max_length=64,
        help_text="SHA-256 hash of uploaded file for duplicate detection",
    )
    raw_file = models.FileField(
        upload_to="bank_statements/%Y/%m/",
        help_text="Original uploaded statement file, stored permanently",
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="bank_statement_imports",
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)
    statement_start_date = models.DateField(
        null=True,
        blank=True,
        help_text="Start date of the statement period",
    )
    statement_end_date = models.DateField(
        null=True,
        blank=True,
        help_text="End date of the statement period",
    )
    import_status = models.CharField(
        max_length=20,
        choices=ImportStatus.choices,
        default=ImportStatus.PENDING,
    )
    source_type = models.CharField(
        max_length=20,
        default="FILE",
        help_text="FILE / MANUAL / COPY_PASTE",
    )
    error_log = models.TextField(
        blank=True,
        default="",
    )
    row_count = models.PositiveIntegerField(
        default=0,
        help_text="Number of transaction rows imported",
    )

    class Meta:
        ordering = ("-uploaded_at",)
        indexes = [
            models.Index(fields=["society", "bank_account"]),
            models.Index(fields=["society", "import_status"]),
            models.Index(fields=["society", "file_hash"]),
            models.Index(fields=["society", "statement_start_date", "statement_end_date"]),
        ]

    def clean(self):
        if self.statement_start_date and self.statement_end_date:
            if self.statement_start_date > self.statement_end_date:
                raise ValidationError(
                    "Statement start date must be before end date."
                )
        if self.bank_account.society != self.society:
            raise ValidationError(
                "Bank account must belong to the same society."
            )

    @staticmethod
    def compute_file_hash(file_obj) -> str:
        """Compute SHA-256 hash of a file. Caller must seek to 0 first."""
        sha256 = hashlib.sha256()
        for chunk in file_obj.chunks():
            sha256.update(chunk)
        return sha256.hexdigest()

    def __str__(self):
        return f"{self.bank_account} — {self.file_name} ({self.import_status})"
