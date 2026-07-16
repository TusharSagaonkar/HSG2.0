import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _


class ShiftHandover(models.Model):
    """Records the handover of gate responsibility from an outgoing guard to an
    incoming guard at the end of a shift.

    Phase 12 — Exit Management. A ``ShiftHandover`` snapshots the count of
    persons currently inside and any pending items at handover time, and tracks
    an acknowledgement lifecycle (``PENDING → ACKNOWLEDGED`` or
    ``PENDING → DISPUTED → ACKNOWLEDGED``). Per-person snapshot rows are stored
    as :class:`ShiftHandoverItem` children.

    Soft-delete follows the established ``is_active`` + ``deleted_at`` pattern
    (matching :class:`SecurityGuard`, :class:`GuardShift`, :class:`Pass`).
    """

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        ACKNOWLEDGED = "acknowledged", _("Acknowledged")
        DISPUTED = "disputed", _("Disputed")

    society = models.ForeignKey(
        "housing.Society",
        on_delete=models.CASCADE,
        related_name="shift_handovers",
        verbose_name=_("society"),
    )
    handover_uuid = models.UUIDField(
        _("handover UUID"),
        default=uuid.uuid4,
        unique=True,
        editable=False,
        db_index=True,
    )
    outgoing_guard = models.ForeignKey(
        "gateops.SecurityGuard",
        on_delete=models.PROTECT,
        related_name="outgoing_handovers",
        verbose_name=_("outgoing guard"),
    )
    incoming_guard = models.ForeignKey(
        "gateops.SecurityGuard",
        on_delete=models.PROTECT,
        related_name="incoming_handovers",
        verbose_name=_("incoming guard"),
    )
    gate = models.ForeignKey(
        "gateops.Gate",
        on_delete=models.PROTECT,
        related_name="shift_handovers",
        verbose_name=_("gate"),
    )
    shift = models.ForeignKey(
        "gateops.GuardShift",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="handovers",
        verbose_name=_("shift"),
    )
    outgoing_assignment = models.ForeignKey(
        "gateops.GuardShiftAssignment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="outgoing_handovers",
        verbose_name=_("outgoing assignment"),
    )
    incoming_assignment = models.ForeignKey(
        "gateops.GuardShiftAssignment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="incoming_handovers",
        verbose_name=_("incoming assignment"),
    )
    status = models.CharField(
        _("status"),
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    inside_count = models.PositiveIntegerField(_("inside count"), default=0)
    pending_items_count = models.PositiveIntegerField(
        _("pending items count"), default=0
    )
    pending_items_summary = models.JSONField(
        _("pending items summary"), default=dict
    )
    outgoing_notes = models.TextField(_("outgoing notes"), blank=True)
    incoming_notes = models.TextField(_("incoming notes"), blank=True)
    dispute_reason = models.TextField(_("dispute reason"), blank=True)
    handed_over_at = models.DateTimeField(_("handed over at"), auto_now_add=True)
    acknowledged_at = models.DateTimeField(
        _("acknowledged at"), null=True, blank=True
    )
    disputed_at = models.DateTimeField(_("disputed at"), null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_shift_handovers",
        verbose_name=_("created by"),
    )
    acknowledged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="acknowledged_shift_handovers",
        verbose_name=_("acknowledged by"),
    )
    is_active = models.BooleanField(_("active"), default=True)
    deleted_at = models.DateTimeField(_("deleted at"), null=True, blank=True)
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)

    class Meta:
        verbose_name = _("Shift Handover")
        verbose_name_plural = _("Shift Handovers")
        ordering = ("-handed_over_at",)
        indexes = [
            models.Index(fields=["society", "status"], name="handover_soc_status_idx"),
            models.Index(fields=["society", "gate"], name="handover_soc_gate_idx"),
            models.Index(fields=["society", "outgoing_guard"], name="handover_soc_out_idx"),
            models.Index(fields=["society", "incoming_guard"], name="handover_soc_in_idx"),
            models.Index(fields=["society", "handed_over_at"], name="handover_soc_date_idx"),
            models.Index(fields=["society", "is_active"], name="handover_soc_act_idx"),
            models.Index(fields=["handover_uuid"], name="handover_uuid_idx"),
        ]

    def __str__(self):
        return (
            f"Handover {self.handover_uuid} — {self.outgoing_guard} → "
            f"{self.incoming_guard} @ {self.gate} [{self.status}]"
        )

    def clean(self):
        super().clean()
        # Cross-society guards: all FK targets must belong to the same society.
        if self.outgoing_guard_id is not None and self.outgoing_guard.society_id != self.society_id:
            raise ValidationError(
                {"outgoing_guard": _("Outgoing guard must belong to the same society.")}
            )
        if self.incoming_guard_id is not None and self.incoming_guard.society_id != self.society_id:
            raise ValidationError(
                {"incoming_guard": _("Incoming guard must belong to the same society.")}
            )
        if self.gate_id is not None and self.gate.society_id != self.society_id:
            raise ValidationError({"gate": _("Gate must belong to the same society.")})
        if self.shift_id is not None and self.shift.society_id != self.society_id:
            raise ValidationError({"shift": _("Shift must belong to the same society.")})
        if (
            self.outgoing_assignment_id is not None
            and self.outgoing_assignment.society_id != self.society_id
        ):
            raise ValidationError(
                {"outgoing_assignment": _("Outgoing assignment must belong to the same society.")}
            )
        if (
            self.incoming_assignment_id is not None
            and self.incoming_assignment.society_id != self.society_id
        ):
            raise ValidationError(
                {"incoming_assignment": _("Incoming assignment must belong to the same society.")}
            )
        # A guard cannot hand over to themselves.
        if (
            self.incoming_guard_id is not None
            and self.outgoing_guard_id == self.incoming_guard_id
        ):
            raise ValidationError(
                {"incoming_guard": _("Incoming guard must differ from outgoing guard.")}
            )
        # acknowledged_at requires status ACKNOWLEDGED.
        if self.acknowledged_at is not None and self.status != self.Status.ACKNOWLEDGED:
            raise ValidationError(
                {"acknowledged_at": _("acknowledged_at requires status ACKNOWLEDGED.")}
            )
        # disputed_at requires status DISPUTED.
        if self.disputed_at is not None and self.status != self.Status.DISPUTED:
            raise ValidationError(
                {"disputed_at": _("disputed_at requires status DISPUTED.")}
            )
        # dispute_reason requires status DISPUTED.
        if self.dispute_reason and self.status != self.Status.DISPUTED:
            raise ValidationError(
                {"dispute_reason": _("dispute_reason requires status DISPUTED.")}
            )

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)
