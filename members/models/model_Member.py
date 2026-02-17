from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class Member(models.Model):
    class MemberRole(models.TextChoices):
        OWNER = "OWNER", "Owner"
        TENANT = "TENANT", "Tenant"
        NOMINEE = "NOMINEE", "Nominee"

    class MemberStatus(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        INACTIVE = "INACTIVE", "Inactive"

    society = models.ForeignKey(
        "housing.Society",
        on_delete=models.CASCADE,
        related_name="members",
    )
    unit = models.ForeignKey(
        "housing.Unit",
        on_delete=models.PROTECT,
        related_name="members",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="society_memberships",
    )
    full_name = models.CharField(max_length=255)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    role = models.CharField(max_length=20, choices=MemberRole.choices)
    status = models.CharField(max_length=20, choices=MemberStatus.choices, default=MemberStatus.ACTIVE)
    receivable_account = models.ForeignKey(
        "accounting.Account",
        on_delete=models.PROTECT,
        related_name="members",
        null=True,
        blank=True,
    )
    start_date = models.DateField(default=timezone.localdate)
    end_date = models.DateField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "housing"
        ordering = ("full_name", "id")
        unique_together = ("society", "unit", "full_name", "role")

    def clean(self):
        if self.unit and self.unit.structure.society_id != self.society_id:
            raise ValidationError("Member unit must belong to the selected society.")
        if self.receivable_account and self.receivable_account.society_id != self.society_id:
            raise ValidationError("Receivable account must belong to the selected society.")
        if self.end_date and self.end_date < self.start_date:
            raise ValidationError("Member end date cannot be before start date.")

    def __str__(self):
        return f"{self.full_name} ({self.role})"
