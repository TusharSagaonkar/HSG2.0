from django.db import models


class StagingLoan(models.Model):
    """T9 staging: loan outstanding balances during migration.

    Each row represents one loan imported from the Loans template, with
    outstanding principal and interest.
    """

    class LoanType(models.TextChoices):
        BANK_LOAN = "BANK_LOAN", "Bank Loan"
        SOCIETY_LOAN = "SOCIETY_LOAN", "Society Loan"
        MEMBER_LOAN = "MEMBER_LOAN", "Member Loan"

    class ValidationStatus(models.TextChoices):
        PENDING = "PENDING", "Pending"
        VALID = "VALID", "Valid"
        INVALID = "INVALID", "Invalid"

    wizard = models.ForeignKey(
        "onboarding.OnboardingWizard",
        on_delete=models.CASCADE,
        related_name="staging_loans",
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
        related_name="loan_rows",
    )
    row_number = models.PositiveIntegerField()
    loan_name = models.CharField(max_length=200)
    loan_type = models.CharField(
        max_length=50, blank=True, choices=LoanType.choices
    )
    outstanding_principal = models.DecimalField(
        max_digits=18, decimal_places=2, default=0
    )
    interest = models.DecimalField(max_digits=18, decimal_places=2, default=0)
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
        return f"{self.loan_name} — {self.outstanding_principal}"
