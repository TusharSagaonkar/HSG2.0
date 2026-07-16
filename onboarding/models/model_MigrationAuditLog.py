from django.conf import settings
from django.db import models


class MigrationAuditLog(models.Model):
    """Append-only audit log specific to the migration process.

    Complements the platform-wide ``AuditLog`` with migration-specific
    before/after state snapshots. Immutability is enforced at the model
    level: ``save()`` rejects updates and ``delete()`` raises
    ``PermissionError`` (pattern from ``auditlog.models.AuditLog``).
    """

    wizard = models.ForeignKey(
        "onboarding.OnboardingWizard",
        on_delete=models.CASCADE,
        related_name="audit_logs",
    )
    society = models.ForeignKey(
        "housing.Society",
        on_delete=models.SET_NULL,
        null=True,
    )
    action = models.CharField(max_length=100)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    timestamp = models.DateTimeField(auto_now_add=True)
    details = models.JSONField(default=dict)
    before_state = models.JSONField(default=dict, blank=True)
    after_state = models.JSONField(default=dict, blank=True)

    class Meta:
        app_label = "onboarding"
        ordering = ["-timestamp"]

    def __str__(self):
        return f"{self.action} — Wizard #{self.wizard_id} @ {self.timestamp:%Y-%m-%d %H:%M:%S}"

    def save(self, *args, **kwargs):
        """Append-only: reject updates, allow inserts only."""
        if self.pk is not None and not kwargs.pop("_force_insert", False):
            raise PermissionError(
                "MigrationAuditLog is append-only; updating existing records is not permitted."
            )
        kwargs["force_insert"] = True
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        """Append-only: deletion is forbidden."""
        raise PermissionError(
            "MigrationAuditLog is append-only; deletion is not permitted."
        )
