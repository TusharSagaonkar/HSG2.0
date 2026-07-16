from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class ApprovalRequest(models.Model):
    """Maker-checker approval request for sensitive operations.

    When a user (maker) performs a sensitive action (e.g., voucher posting,
    ownership transfer), an ApprovalRequest is created. The action is only
    executed when an authorized checker approves it.

    The ``entity_type`` and ``entity_id`` identify the object being approved.
    The ``action`` describes what will happen upon approval.
    The ``payload`` stores the data needed to execute the action.
    """

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        APPROVED = "approved", _("Approved")
        REJECTED = "rejected", _("Rejected")
        CANCELLED = "cancelled", _("Cancelled")
        EXPIRED = "expired", _("Expired")

    class Action(models.TextChoices):
        VOUCHER_POST = "voucher_post", _("Voucher Post")
        VOUCHER_REVERSE = "voucher_reverse", _("Voucher Reverse")
        OWNERSHIP_TRANSFER = "ownership_transfer", _("Ownership Transfer")
        ROLE_CHANGE = "role_change", _("Role Change")
        MEMBERSHIP_DEACTIVATE = "membership_deactivate", _("Membership Deactivate")
        BULK_BILLING = "bulk_billing", _("Bulk Billing")
        YEAR_END_CLOSE = "year_end_close", _("Year End Close")
        CUSTOM = "custom", _("Custom")

    society = models.ForeignKey(
        "housing.Society",
        on_delete=models.CASCADE,
        related_name="approval_requests",
    )
    action = models.CharField(max_length=30, choices=Action.choices)
    entity_type = models.CharField(max_length=50)
    entity_id = models.CharField(max_length=50)
    payload = models.JSONField(
        default=dict,
        help_text=_("Data needed to execute the action upon approval."),
    )

    # Maker (requester)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="approval_requests_made",
    )
    requested_at = models.DateTimeField(auto_now_add=True)
    reason = models.CharField(max_length=500, blank=True, default="")

    # Checker (approver)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approval_requests_reviewed",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_comment = models.CharField(max_length=500, blank=True, default="")

    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.PENDING,
    )

    # Escalation
    escalation_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="escalated_approvals",
    )
    escalation_at = models.DateTimeField(null=True, blank=True)

    # Expiry
    expires_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("Auto-expiry timestamp."),
    )

    class Meta:
        app_label = "societies"
        ordering = ("-requested_at",)
        indexes = [
            models.Index(fields=["society", "status"]),
            models.Index(fields=["society", "action", "status"]),
            models.Index(fields=["requested_by", "status"]),
            models.Index(fields=["status", "expires_at"]),
        ]

    def __str__(self):
        return (
            f"{self.get_action_display()} — "
            f"{self.entity_type}:{self.entity_id} ({self.status})"
        )

    @property
    def is_pending(self):
        return self.status == self.Status.PENDING

    def approve(self, *, reviewer, comment=""):
        """Approve the request."""
        from django.utils import timezone

        self.status = self.Status.APPROVED
        self.reviewed_by = reviewer
        self.reviewed_at = timezone.now()
        self.review_comment = comment
        self.save(
            update_fields=["status", "reviewed_by", "reviewed_at", "review_comment"],
        )

    def reject(self, *, reviewer, comment=""):
        """Reject the request."""
        from django.utils import timezone

        self.status = self.Status.REJECTED
        self.reviewed_by = reviewer
        self.reviewed_at = timezone.now()
        self.review_comment = comment
        self.save(
            update_fields=["status", "reviewed_by", "reviewed_at", "review_comment"],
        )

    def cancel(self, *, requester=None):
        """Cancel the request (by maker)."""
        self.status = self.Status.CANCELLED
        self.save(update_fields=["status"])
