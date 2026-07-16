from django.db import models


class StagingFixedAsset(models.Model):
    """T7 staging: fixed asset register during migration.

    Each row represents one fixed asset imported from the Fixed Assets
    template, with gross value, accumulated depreciation, and net value.
    """

    class ValidationStatus(models.TextChoices):
        PENDING = "PENDING", "Pending"
        VALID = "VALID", "Valid"
        INVALID = "INVALID", "Invalid"

    wizard = models.ForeignKey(
        "onboarding.OnboardingWizard",
        on_delete=models.CASCADE,
        related_name="staging_fixed_assets",
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
        related_name="fixed_asset_rows",
    )
    row_number = models.PositiveIntegerField()
    asset_name = models.CharField(max_length=200)
    asset_category = models.CharField(max_length=100, blank=True)
    gross_value = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    depreciation = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    net_value = models.DecimalField(max_digits=18, decimal_places=2, default=0)
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
        return f"{self.asset_name} — {self.net_value}"
