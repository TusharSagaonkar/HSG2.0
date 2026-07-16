from django.db import models


class StagingBankOpening(models.Model):
    """T5 staging: per-bank opening balances during migration.

    Each row represents one bank account's opening balance imported from the
    Bank Opening Balances template.
    """

    class ValidationStatus(models.TextChoices):
        PENDING = "PENDING", "Pending"
        VALID = "VALID", "Valid"
        INVALID = "INVALID", "Invalid"

    wizard = models.ForeignKey(
        "onboarding.OnboardingWizard",
        on_delete=models.CASCADE,
        related_name="staging_bank_opening",
    )
    society = models.ForeignKey(
        "housing.Society",
        on_delete=models.SET_NULL,
        null=True,
    )
    upload_batch = models.ForeignKey(
        "onboarding.UploadBatch",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="bank_opening_rows",
    )
    row_number = models.PositiveIntegerField()
    bank_name = models.CharField(max_length=200)
    account_number = models.CharField(max_length=50)
    ifsc = models.CharField(max_length=20, blank=True)
    branch = models.CharField(max_length=200, blank=True)
    opening_balance = models.DecimalField(
        max_digits=18, decimal_places=2, default=0
    )
    account_code = models.CharField(max_length=50, blank=True)
    raw_data = models.JSONField(default=dict)
    validation_status = models.CharField(
        max_length=20,
        choices=ValidationStatus.choices,
        default=ValidationStatus.PENDING,
    )
    validation_errors = models.JSONField(default=list)
    is_approved = models.BooleanField(default=False)

    class Meta:
        app_label = "onboarding"
        ordering = ["upload_batch", "row_number"]

    def __str__(self):
        return f"{self.bank_name} — {self.account_number}"
