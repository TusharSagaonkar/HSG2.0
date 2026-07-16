from django.db import models


class StagingMemberOutstanding(models.Model):
    """T3 staging: per-flat member outstanding during migration.

    Each row represents one member/unit's outstanding balance imported from
    the Member Outstanding template.
    """

    class ValidationStatus(models.TextChoices):
        PENDING = "PENDING", "Pending"
        VALID = "VALID", "Valid"
        INVALID = "INVALID", "Invalid"

    wizard = models.ForeignKey(
        "onboarding.OnboardingWizard",
        on_delete=models.CASCADE,
        related_name="staging_member_outstanding",
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
        related_name="member_outstanding_rows",
    )
    row_number = models.PositiveIntegerField()
    unit_identifier = models.CharField(max_length=50)
    member_name = models.CharField(max_length=200)
    outstanding_amount = models.DecimalField(
        max_digits=18, decimal_places=2, default=0
    )
    advance_maintenance = models.DecimalField(
        max_digits=18, decimal_places=2, default=0
    )
    credit_balance = models.DecimalField(
        max_digits=18, decimal_places=2, default=0
    )
    late_fees = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    interest_receivable = models.DecimalField(
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
        return f"{self.unit_identifier} — {self.member_name}"
