import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class GateEvent(models.Model):
    """The central unified gate event / visit session record.

    A single ``GateEvent`` row represents one visitor's journey through the
    gate lifecycle: invitation -> arrival -> approval -> entry -> exit. The
    ``status`` field tracks the current state machine position, while the
    timestamp fields (``arrived_at``, ``approved_at``, ``entered_at``,
    ``exited_at``, ``auto_close_at``) record when each transition occurred.

    ``event_uuid`` is the externally-safe identifier (exposed to apps/SMS/QR
    codes); the numeric ``id`` is used only internally and for FK joins.

    The ``rule_evaluated`` FK links to the :class:`RuleEvaluation` log row that
    produced the decision for this event, while ``rule_action`` caches the
    action string for fast filtering without a join.
    """

    class EventType(models.TextChoices):
        INVITATION = "invitation", _("Invitation")
        ARRIVAL = "arrival", _("Arrival")
        ENTRY = "entry", _("Entry")
        EXIT = "exit", _("Exit")
        AUTO_CLOSE = "auto_close", _("Auto Close")
        CANCELLED = "cancelled", _("Cancelled")
        EXPIRED = "expired", _("Expired")
        REJECTED = "rejected", _("Rejected")

    class Status(models.TextChoices):
        INVITED = "invited", _("Invited")
        ARRIVED = "arrived", _("Arrived")
        APPROVED = "approved", _("Approved")
        REJECTED = "rejected", _("Rejected")
        ENTERED = "entered", _("Entered")
        EXITED = "exited", _("Exited")
        AUTO_CLOSED = "auto_closed", _("Auto Closed")
        CANCELLED = "cancelled", _("Cancelled")
        EXPIRED = "expired", _("Expired")

    class Direction(models.TextChoices):
        INBOUND = "inbound", _("Inbound")
        OUTBOUND = "outbound", _("Outbound")

    society = models.ForeignKey(
        "housing.Society",
        on_delete=models.CASCADE,
        related_name="gate_events",
        verbose_name=_("society"),
    )
    event_uuid = models.UUIDField(
        _("event UUID"), default=uuid.uuid4, unique=True, editable=False, db_index=True
    )
    gate = models.ForeignKey(
        "gateops.Gate",
        on_delete=models.PROTECT,
        related_name="events",
        verbose_name=_("gate"),
    )
    guard = models.ForeignKey(
        "gateops.SecurityGuard",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="events",
        verbose_name=_("guard"),
    )
    person = models.ForeignKey(
        "gateops.Person",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="gate_events",
        verbose_name=_("person"),
    )
    visitor_category = models.ForeignKey(
        "gateops.VisitorCategory",
        on_delete=models.PROTECT,
        related_name="events",
        verbose_name=_("visitor category"),
    )
    vehicle = models.ForeignKey(
        "parking.Vehicle",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="gate_events",
        verbose_name=_("vehicle"),
    )
    # Phase 6: visitor/non-resident vehicle (distinct from `vehicle` which
    # points to parking.Vehicle for resident-owned vehicles). Additive and
    # non-breaking; nullable so existing events are unaffected.
    gate_vehicle = models.ForeignKey(
        "gateops.GateVehicle",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="gate_events",
        verbose_name=_("gate vehicle"),
    )
    pass_ref = models.ForeignKey(
        "gateops.Pass",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="events",
        verbose_name=_("pass"),
    )
    event_type = models.CharField(
        _("event type"), max_length=20, choices=EventType.choices
    )
    status = models.CharField(
        _("status"), max_length=20, choices=Status.choices, default=Status.INVITED
    )
    direction = models.CharField(
        _("direction"), max_length=10, choices=Direction.choices, default=Direction.INBOUND
    )
    purpose = models.TextField(_("purpose"), blank=True)
    expected_arrival_at = models.DateTimeField(_("expected arrival at"), null=True, blank=True)
    arrived_at = models.DateTimeField(_("arrived at"), null=True, blank=True)
    approved_at = models.DateTimeField(_("approved at"), null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_events",
        verbose_name=_("approved by"),
    )
    entered_at = models.DateTimeField(_("entered at"), null=True, blank=True)
    exited_at = models.DateTimeField(_("exited at"), null=True, blank=True)
    auto_close_at = models.DateTimeField(_("auto close at"), null=True, blank=True)
    photo_url = models.URLField(_("photo URL"), blank=True)
    id_verified = models.BooleanField(_("ID verified"), default=False)
    notes = models.TextField(_("notes"), blank=True)
    rule_evaluated = models.ForeignKey(
        "gateops.RuleEvaluation",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="gate_events",
        verbose_name=_("rule evaluation"),
    )
    rule_action = models.CharField(_("rule action"), max_length=30, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_gate_events",
        verbose_name=_("created by"),
    )
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)

    class Meta:
        verbose_name = _("gate event")
        verbose_name_plural = _("gate events")
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["society", "status"], name="gateevt_status_idx"),
            models.Index(fields=["society", "entered_at"], name="gateevt_entered_idx"),
            models.Index(fields=["society", "created_at"], name="gateevt_created_idx"),
            models.Index(fields=["event_uuid"], name="gateevt_uuid_idx"),
        ]

    def __str__(self):
        return f"GateEvent {self.event_uuid} — {self.status}"

    def clean(self):
        super().clean()
        # entered_at implies the visitor has physically entered: status must be
        # ENTERED or a later terminal state (EXITED / AUTO_CLOSED).
        if self.entered_at is not None and self.status not in {
            self.Status.ENTERED,
            self.Status.EXITED,
            self.Status.AUTO_CLOSED,
        }:
            raise ValidationError(
                {"entered_at": _("entered_at requires status ENTERED or later.")}
            )
        # exited_at implies the visit has ended: status must be EXITED or AUTO_CLOSED.
        if self.exited_at is not None and self.status not in {
            self.Status.EXITED,
            self.Status.AUTO_CLOSED,
        }:
            raise ValidationError(
                {"exited_at": _("exited_at requires status EXITED or AUTO_CLOSED.")}
            )
        # Exit must occur strictly after entry.
        if self.entered_at is not None and self.exited_at is not None:
            if self.exited_at <= self.entered_at:
                raise ValidationError(
                    {"exited_at": _("exited_at must be after entered_at.")}
                )
        # Approval must occur at or after arrival (arrival precedes approval).
        if self.arrived_at is not None and self.approved_at is not None:
            if self.approved_at < self.arrived_at:
                raise ValidationError(
                    {"approved_at": _("approved_at must be after arrived_at.")}
                )
        # A scheduled auto-close only makes sense while the visitor is still
        # inside; it must be a future timestamp.
        if self.auto_close_at is not None and self.status == self.Status.ENTERED:
            if self.auto_close_at <= timezone.now():
                raise ValidationError(
                    {"auto_close_at": _("auto_close_at must be in the future.")}
                )

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)
