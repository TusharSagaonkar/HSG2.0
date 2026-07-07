from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

# NOTE: `gate_event` FK is added in Phase 3 (see class docstring below).


class RuleEvaluation(models.Model):
    """Append-only log of every rule-engine evaluation.

    This is a lighter log than :class:`GateOpsAuditLog` — it records the rule
    engine's decision (matched rule, action taken, execution time, error) for
    debugging, analytics, and audit. One row is written per ``evaluate()``
    call, including the no-match case.

    Intent: this model is append-only. Unlike ``GateOpsAuditLog`` we do NOT
    override ``save()`` / ``delete()`` to hard-enforce immutability (to keep
    the model lightweight and avoid surprising callers that bulk-create or
    refresh instances). Callers MUST treat existing rows as immutable: never
    update or delete them. New rows are created exclusively via
    :meth:`RuleEngineService._log_evaluation`.

    The ``gate_event`` FK references the Phase 3 :class:`GateEvent` model and
    links an evaluation to the visitor session it decided on. It is nullable
    because some evaluations (e.g. dry-runs, pre-invitation checks) may not be
    tied to a persisted gate event.
    """

    class ActionTaken(models.TextChoices):
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
        NO_MATCH = "no_match", _("No Match")
        ERROR = "error", _("Error")

    society = models.ForeignKey(
        "housing.Society",
        on_delete=models.CASCADE,
        related_name="gateops_rule_evaluations",
    )
    rule = models.ForeignKey(
        "gateops.Rule",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="evaluations",
    )
    gate_event = models.ForeignKey(
        "gateops.GateEvent",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="rule_evaluations",
        verbose_name=_("gate event"),
    )
    evaluated_at = models.DateTimeField(auto_now_add=True)
    input_context = models.JSONField(default=dict)
    matched_conditions = models.JSONField(default=dict, blank=True)
    action_taken = models.CharField(max_length=30, choices=ActionTaken.choices)
    execution_time_ms = models.IntegerField(default=0)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="gateops_rule_evaluations",
    )
    error_message = models.TextField(blank=True)

    class Meta:
        verbose_name = _("Rule Evaluation Log")
        verbose_name_plural = _("Rule Evaluation Logs")
        ordering = ("-evaluated_at", "-id")
        indexes = [
            models.Index(fields=["society", "-evaluated_at"], name="ruleeval_time_idx"),
            models.Index(fields=["society", "rule"], name="ruleeval_rule_idx"),
            models.Index(fields=["society", "action_taken"], name="ruleeval_act_idx"),
        ]

    def __str__(self):
        return f"{self.action_taken} @ {self.evaluated_at:%Y-%m-%d %H:%M:%S}"
