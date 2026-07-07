from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _


class RuleAction(models.Model):
    """An action executed when a :class:`Rule` matches.

    A rule may have multiple actions executed in ``execution_order``. The rule
    engine executes the first matching action's effect and records the action
    code on the :class:`RuleEvaluation` log.

    ``parameters`` stores action-specific configuration, e.g.::

        {"notify_channels": ["push", "sms"], "template": "delivery_arrival"}
        {"escalate_to": "security_supervisor", "timeout_minutes": 10}
    """

    class ActionType(models.TextChoices):
        AUTO_APPROVE = "auto_approve", _("Auto Approve")
        REJECT = "reject", _("Reject")
        REQUIRE_APPROVAL = "require_approval", _("Require Approval")
        REQUIRE_RESIDENT_APPROVAL = (
            "require_resident_approval",
            _("Require Resident Approval"),
        )
        NOTIFY_SECURITY = "notify_security", _("Notify Security")
        EMERGENCY_OVERRIDE = "emergency_override", _("Emergency Override")
        DIRECT_ENTRY = "direct_entry", _("Direct Entry")
        FLAG_FOR_REVIEW = "flag_for_review", _("Flag for Review")
        SEND_NOTIFICATION = "send_notification", _("Send Notification")
        ESCALATE = "escalate", _("Escalate")

    rule = models.ForeignKey(
        "gateops.Rule",
        on_delete=models.CASCADE,
        related_name="actions",
    )
    action = models.CharField(max_length=30, choices=ActionType.choices)
    parameters = models.JSONField(default=dict, blank=True)
    execution_order = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Rule Action")
        verbose_name_plural = _("Rule Actions")
        ordering = ("rule", "execution_order")
        indexes = [
            models.Index(fields=["rule", "execution_order"], name="ruleact_order_idx"),
        ]

    def clean(self):
        if self.execution_order is None or self.execution_order < 0:
            raise ValidationError(
                {"execution_order": _("execution_order must be >= 0.")}
            )

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.action} (order={self.execution_order})"
