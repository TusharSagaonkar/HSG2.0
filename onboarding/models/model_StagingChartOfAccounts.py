from django.db import models


class StagingChartOfAccounts(models.Model):
    """T1 staging: custom account additions during migration.

    Each row represents one account line imported from the Chart of
    Accounts template. Validation status and errors are tracked per row
    for the validation engine.
    """

    class Nature(models.TextChoices):
        ASSET = "ASSET", "Asset"
        LIABILITY = "LIABILITY", "Liability"
        INCOME = "INCOME", "Income"
        EXPENSE = "EXPENSE", "Expense"
        EQUITY = "EQUITY", "Equity"

    class ValidationStatus(models.TextChoices):
        PENDING = "PENDING", "Pending"
        VALID = "VALID", "Valid"
        INVALID = "INVALID", "Invalid"

    wizard = models.ForeignKey(
        "onboarding.OnboardingWizard",
        on_delete=models.CASCADE,
        related_name="staging_coa",
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
        related_name="coa_rows",
    )
    row_number = models.PositiveIntegerField()
    account_code = models.CharField(max_length=50)
    account_name = models.CharField(max_length=200)
    account_group = models.CharField(max_length=100, blank=True)
    account_type = models.CharField(max_length=50, blank=True)
    parent_code = models.CharField(max_length=50, blank=True)
    nature = models.CharField(max_length=20, blank=True, choices=Nature.choices)
    opening_debit = models.DecimalField(
        max_digits=18, decimal_places=2, default=0
    )
    opening_credit = models.DecimalField(
        max_digits=18, decimal_places=2, default=0
    )
    is_system_account = models.BooleanField(default=False)
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
        return f"{self.account_code} — {self.account_name}"
