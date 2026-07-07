from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class GateEventApproval(models.Model):
    """Approval request/decision record for a :class:`GateEvent`.

    When a gate event requires human approval (e.g. a rule action of
    ``require_approval`` or ``require_resident_approval``), one
    ``GateEventApproval`` row is created per approval request. If the request
    is escalated, a new row is created with ``decision=ESCALATED`` and a fresh
    request is issued to the next approver.

    ``decision_method`` records the channel through which the decision was
    returned (app, SMS, WhatsApp, voice call, in person) for audit and
    analytics.
    """

    class Decision(models.TextChoices):
        PENDING = "pending", _("Pending")
        APPROVED = "approved", _("Approved")
        REJECTED = "rejected", _("Rejected")
        ESCALATED = "escalated", _("Escalated")

    class DecisionMethod(models.TextChoices):
        APP = "app", _("Mobile App")
        SMS = "sms", _("SMS")
        WHATSAPP = "whatsapp", _("WhatsApp")
        VOICE = "voice", _("Voice Call")
        IN_PERSON = "in_person", _("In Person")

    gate_event = models.ForeignKey(
        "gateops.GateEvent",
        on_delete=models.CASCADE,
        related_name="approvals",
        verbose_name=_("gate event"),
    )
    society = models.ForeignKey(
        "housing.Society",
        on_delete=models.CASCADE,
        related_name="gate_event_approvals",
        verbose_name=_("society"),
    )
    requested_at = models.DateTimeField(_("requested at"), auto_now_add=True)
    requested_from = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approval_requests",
        verbose_name=_("requested from"),
    )
    decision = models.CharField(
        _("decision"), max_length=20, choices=Decision.choices, default=Decision.PENDING
    )
    decided_at = models.DateTimeField(_("decided at"), null=True, blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approval_decisions",
        verbose_name=_("decided by"),
    )
    decision_method = models.CharField(
        _("decision method"), max_length=20, choices=DecisionMethod.choices, blank=True
    )
    notes = models.TextField(_("notes"), blank=True)
    timeout_at = models.DateTimeField(_("timeout at"), null=True, blank=True)

    class Meta:
        verbose_name = _("gate event approval")
        verbose_name_plural = _("gate event approvals")
        ordering = ("-requested_at",)
        indexes = [
            models.Index(fields=["gate_event"], name="gateappr_evt_idx"),
            models.Index(fields=["society", "decision"], name="gateappr_dec_idx"),
        ]

    def __str__(self):
        return f"Approval for {self.gate_event_id} — {self.decision}"
