from django.db import models


class StagingFund(models.Model):
    """T10 staging: restricted fund balances during migration.

    Each row represents one fund imported from the Funds template, with its
    balance and linked account code.
    """

    class ValidationStatus(models.TextChoices):
        PENDING = "PENDING", "Pending"
        VALID = "VALID", "Valid"
        INVALID = "INVALID", "Invalid"

    wizard = models.ForeignKey(
        "onboarding.OnboardingWizard",
        on_delete=models.CASCADE,
        related_name="staging_funds",
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
        related_name="fund_rows",
    )
    row_number = models.PositiveIntegerField()
    fund_name = models.CharField(max_length=200)
    fund_type = models.CharField(max_length=50, blank=True)
    balance = models.DecimalField(max_digits=18, decimal_places=2, default=0)
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
        return f"{self.fund_name} — {self.balance}"
