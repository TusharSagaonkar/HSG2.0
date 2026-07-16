from django.db import models


class StagingVendorOutstanding(models.Model):
    """T4 staging: per-vendor outstanding during migration.

    Each row represents one vendor's outstanding balance imported from the
    Vendor Outstanding template.
    """

    class ValidationStatus(models.TextChoices):
        PENDING = "PENDING", "Pending"
        VALID = "VALID", "Valid"
        INVALID = "INVALID", "Invalid"

    wizard = models.ForeignKey(
        "onboarding.OnboardingWizard",
        on_delete=models.CASCADE,
        related_name="staging_vendor_outstanding",
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
        related_name="vendor_outstanding_rows",
    )
    row_number = models.PositiveIntegerField()
    vendor_name = models.CharField(max_length=200)
    outstanding_amount = models.DecimalField(
        max_digits=18, decimal_places=2, default=0
    )
    advance_paid = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    retention = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    security_deposit = models.DecimalField(
        max_digits=18, decimal_places=2, default=0
    )
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
        return f"{self.vendor_name} — {self.outstanding_amount}"
