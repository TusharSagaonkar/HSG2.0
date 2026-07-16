from django.conf import settings
from django.db import models


class WizardStepLog(models.Model):
    """Audit trail of each wizard step completion.

    Append-only: ``save()`` rejects updates and ``delete()`` raises
    ``PermissionError`` (pattern from ``auditlog.models.AuditLog``).
    """

    class Status(models.TextChoices):
        STARTED = "STARTED", "Started"
        COMPLETED = "COMPLETED", "Completed"
        SKIPPED = "SKIPPED", "Skipped"
        FAILED = "FAILED", "Failed"

    wizard = models.ForeignKey(
        "onboarding.OnboardingWizard",
        on_delete=models.CASCADE,
        related_name="step_logs",
    )
    step_number = models.PositiveIntegerField()
    step_name = models.CharField(max_length=100)
    status = models.CharField(max_length=20, choices=Status.choices)
    data_snapshot = models.JSONField(default=dict)
    completed_at = models.DateTimeField(null=True, blank=True)
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )

    class Meta:
        app_label = "onboarding"
        ordering = ["wizard", "step_number"]
        unique_together = [["wizard", "step_number"]]

    def __str__(self):
        return f"Step {self.step_number} ({self.status}) — Wizard #{self.wizard_id}"

    def save(self, *args, **kwargs):
        """Append-only: reject updates, allow inserts only."""
        if self.pk is not None and not kwargs.pop("_force_insert", False):
            raise PermissionError(
                "WizardStepLog is append-only; updating existing records is not permitted."
            )
        kwargs["force_insert"] = True
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        """Append-only: deletion is forbidden."""
        raise PermissionError(
            "WizardStepLog is append-only; deletion is not permitted."
        )
