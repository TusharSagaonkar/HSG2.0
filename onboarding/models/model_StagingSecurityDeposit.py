from django.db import models


class StagingSecurityDeposit(models.Model):
    """T8 staging: security deposit liabilities during migration.

    Each row represents one security deposit imported from the Security
    Deposits template.
    """

    class ValidationStatus(models.TextChoices):
        PENDING = "PENDING", "Pending"
        VALID = "VALID", "Valid"
        INVALID = "INVALID", "Invalid"

    wizard = models.ForeignKey(
        "onboarding.OnboardingWizard",
        on_delete=models.CASCADE,
        related_name="staging_security_deposits",
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
        related_name="security_deposit_rows",
    )
    row_number = models.PositiveIntegerField()
    description = models.CharField(max_length=200)
    amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    against_account = models.CharField(max_length=50, blank=True)
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
        return f"{self.description} — {self.amount}"
