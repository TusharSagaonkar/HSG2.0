from django.conf import settings
from django.db import models

from societies.managers import TenantManager


class UploadBatch(models.Model):
    """Tracks each file upload for a template type within a wizard.

    Template types T1–T10 map to the 10 staging tables. Each upload creates
    one ``UploadBatch`` row and N staging rows. Tenant-scoped via
    :class:`TenantManager`.
    """

    class TemplateType(models.TextChoices):
        CHART_OF_ACCOUNTS = "CHART_OF_ACCOUNTS", "Chart of Accounts"
        TRIAL_BALANCE = "TRIAL_BALANCE", "Trial Balance"
        MEMBER_OUTSTANDING = "MEMBER_OUTSTANDING", "Member Outstanding"
        VENDOR_OUTSTANDING = "VENDOR_OUTSTANDING", "Vendor Outstanding"
        BANK_OPENING = "BANK_OPENING", "Bank Opening Balances"
        CASH_OPENING = "CASH_OPENING", "Cash Opening Balance"
        FIXED_ASSETS = "FIXED_ASSETS", "Fixed Assets"
        SECURITY_DEPOSITS = "SECURITY_DEPOSITS", "Security Deposits"
        LOANS = "LOANS", "Loans"
        FUNDS = "FUNDS", "Funds"

    class Status(models.TextChoices):
        UPLOADED = "UPLOADED", "Uploaded"
        VALIDATED = "VALIDATED", "Validated"
        APPROVED = "APPROVED", "Approved"
        COMMITTED = "COMMITTED", "Committed"
        DELETED = "DELETED", "Deleted"

    wizard = models.ForeignKey(
        "onboarding.OnboardingWizard",
        on_delete=models.CASCADE,
        related_name="upload_batches",
    )
    society = models.ForeignKey(
        "housing.Society",
        on_delete=models.SET_NULL,
        null=True,
    )
    template_type = models.CharField(max_length=50, choices=TemplateType.choices)
    file_name = models.CharField(max_length=255)
    file_path = models.CharField(max_length=500, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    row_count = models.PositiveIntegerField(default=0)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.UPLOADED,
    )
    validation_summary = models.JSONField(default=dict)

    objects = TenantManager()

    class Meta:
        app_label = "onboarding"
        ordering = ["-uploaded_at"]

    def __str__(self):
        return f"{self.template_type} — {self.file_name} ({self.status})"
