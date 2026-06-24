from django.db import models
from django.conf import settings


class ReconciliationHistory(models.Model):
    """
    Immutable audit log recording every state change on a ReconciliationLink.

    Created automatically via signals when a ReconciliationLink's status
    or match_type changes. Never modified or deleted.
    """

    class Action(models.TextChoices):
        CREATED = "CREATED", "Created"
        UPDATED = "UPDATED", "Updated"
        UNMATCHED = "UNMATCHED", "Unmatched"
        REVERSED = "REVERSED", "Reversed"
        FORCE_MATCHED = "FORCE_MATCHED", "Force Matched"
        CONFIRMED = "CONFIRMED", "Confirmed"
        DUPLICATE = "DUPLICATE", "Duplicate Flagged"
        EXCEPTION = "EXCEPTION", "Exception Marked"
        IGNORED = "IGNORED", "Ignored"

    reconciliation_link = models.ForeignKey(
        "reconciliation.ReconciliationLink",
        on_delete=models.CASCADE,
        related_name="history",
    )
    action = models.CharField(
        max_length=30,
        choices=Action.choices,
    )
    previous_status = models.CharField(
        max_length=20,
        blank=True,
        default="",
    )
    new_status = models.CharField(
        max_length=20,
    )
    previous_match_type = models.CharField(
        max_length=20,
        blank=True,
        default="",
    )
    new_match_type = models.CharField(
        max_length=20,
        blank=True,
        default="",
    )
    previous_confidence = models.IntegerField(
        null=True,
        blank=True,
    )
    new_confidence = models.IntegerField(
        null=True,
        blank=True,
    )
    performed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="reconciliation_history",
        null=True,
        blank=True,
    )
    performed_at = models.DateTimeField(auto_now_add=True)
    details = models.JSONField(
        default=dict,
        help_text="Additional context about the action (e.g., reason, old values)",
    )

    class Meta:
        ordering = ("-performed_at",)
        indexes = [
            models.Index(fields=["reconciliation_link", "performed_at"]),
            models.Index(fields=["performed_by", "performed_at"]),
            models.Index(fields=["action", "performed_at"]),
        ]
        verbose_name_plural = "Reconciliation Histories"

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValueError(
                "ReconciliationHistory records are immutable. "
                "Create a new record instead of modifying an existing one."
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError(
            "ReconciliationHistory records cannot be deleted."
        )

    def __str__(self):
        return (
            f"Link#{self.reconciliation_link_id} "
            f"[{self.action}] "
            f"{self.previous_status} → {self.new_status} "
            f"at {self.performed_at}"
        )