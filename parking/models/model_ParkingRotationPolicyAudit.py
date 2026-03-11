from django.conf import settings
from django.db import models
from django.utils import timezone


class ParkingRotationPolicyAudit(models.Model):
    policy = models.ForeignKey(
        "parking.ParkingRotationPolicy",
        on_delete=models.CASCADE,
        related_name="audit_logs",
    )
    old_values = models.JSONField(default=dict)
    new_values = models.JSONField(default=dict)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="parking_rotation_policy_audits",
    )
    change_reason = models.TextField(blank=True)
    changed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        app_label = "parking"
        ordering = ("-changed_at", "-id")

    def __str__(self):
        return f"Policy #{self.policy_id} @ {self.changed_at:%Y-%m-%d %H:%M:%S}"
