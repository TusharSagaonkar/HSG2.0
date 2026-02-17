from django.conf import settings
from django.db import models


class PeriodStatusLog(models.Model):
    class Action(models.TextChoices):
        CLOSED = "CLOSED", "Closed"
        OPENED = "OPENED", "Opened"

    period = models.ForeignKey(
        "accounting.AccountingPeriod",
        on_delete=models.CASCADE,
        related_name="status_logs",
    )
    action = models.CharField(max_length=10, choices=Action.choices)
    reason = models.CharField(max_length=255, blank=True)
    performed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="period_status_actions",
    )
    performed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-performed_at", "-id")

    def __str__(self):
        return f"{self.period} {self.action} at {self.performed_at:%Y-%m-%d %H:%M:%S}"
