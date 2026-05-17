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
    share_balance = models.PositiveIntegerField(default=0)
    join_date = models.DateField(default=timezone.localdate)
    exit_date = models.DateField(blank=True, null=True)

    class Meta:
        app_label = "housing"
        ordering = ("full_name", "id")
        unique_together = ("society", "unit", "full_name", "role")
        indexes = [
            models.Index(fields=["share_balance"]),
            models.Index(fields=["join_date"]),
            models.Index(fields=["status"]),
        ]

    def clean(self):
        if self.unit and self.unit.structure.society_id != self.society_id:
            raise ValidationError("Member unit must belong to the selected society.")
        if self.receivable_account and self.receivable_account.society_id != self.society_id:
            raise ValidationError("Receivable account must belong to the selected society.")
        if self.end_date and self.end_date < self.start_date:
            raise ValidationError("Member end date cannot be before start date.")
        if self.exit_date and self.join_date and self.exit_date < self.join_date:
            raise ValidationError("Exit date cannot be before join date.")
        if self.share_balance < 0:
            raise ValidationError("Share balance cannot be negative.")

    @property
    def active_nominees(self):
        """Return active nominees for this member."""
        from members.models.model_Nominee import Nominee
        return Nominee.objects.filter(member=self, is_active=True).order_by('priority_order')

    @property
    def total_share_value(self):
        """
        Calculate total monetary value of member's shares.
        Returns share_balance * society's share value (to be implemented via SocietyConfig).
        """
        # TODO: Replace with actual share value from SocietyConfig when available
        return self.share_balance * 0

    @property
    def is_active_member(self):
        """
        Returns True if member status is ACTIVE and exit_date is None or in future.
        """
        from django.utils import timezone
        if self.status != self.MemberStatus.ACTIVE:
            return False
        if self.exit_date is None:
            return True
        return self.exit_date >= timezone.localdate()

    def get_share_history(self):
        """
        Return related ShareLedger entries (to be implemented when ShareLedger model exists).
        """
        # TODO: Replace with actual ShareLedger query when model is available
        # Example: return self.share_transactions.all()
        from django.db.models import QuerySet
        return QuerySet(model=None).none()

    def __str__(self):
        share_info = f" ({self.share_balance} shares)" if self.share_balance else ""
        return f"{self.full_name} ({self.role}){share_info}"
