from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class ImpersonationSession(models.Model):
    """Tracks super-admin impersonation sessions for audit and auto-expiry.

    Super-admins must explicitly start an impersonation session with a reason
    and target society. Every action within the session is logged. Sessions
    auto-expire after a configurable timeout.
    """

    class Status(models.TextChoices):
        ACTIVE = "active", _("Active")
        ENDED = "ended", _("Ended")
        EXPIRED = "expired", _("Expired")

    impersonator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="impersonation_sessions_started",
        help_text=_("The super-admin who initiated the impersonation."),
    )
    target_society = models.ForeignKey(
        "housing.Society",
        on_delete=models.CASCADE,
        related_name="impersonation_sessions",
        help_text=_("The society being impersonated into."),
    )
    reason = models.CharField(
        max_length=500,
        help_text=_("Required: reason for impersonation."),
    )
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.ACTIVE,
    )
    started_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(
        help_text=_("Auto-expiry timestamp (default: 1 hour from start)."),
    )
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=500, blank=True, default="")

    class Meta:
        app_label = "societies"
        ordering = ("-started_at",)
        indexes = [
            models.Index(fields=["impersonator", "status"]),
            models.Index(fields=["target_society", "status"]),
            models.Index(fields=["status", "expires_at"]),
        ]

    def __str__(self):
        return f"Impersonation by {self.impersonator} → {self.target_society} ({self.status})"

    @property
    def is_active(self):
        from django.utils import timezone

        return self.status == self.Status.ACTIVE and self.expires_at > timezone.now()

    def end(self):
        """End the impersonation session."""
        from django.utils import timezone

        if self.status == self.Status.ACTIVE:
            self.status = self.Status.ENDED
            self.ended_at = timezone.now()
            self.save(update_fields=["status", "ended_at"])
