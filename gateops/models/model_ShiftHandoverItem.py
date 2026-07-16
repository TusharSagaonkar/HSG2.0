from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _


class ShiftHandoverItem(models.Model):
    """Per-person snapshot record linking a :class:`ShiftHandover` to each
    :class:`GateEvent` that was "currently inside" at handover time.

    Phase 12 — Exit Management. This gives the incoming guard a line-item view
    of who they are taking responsibility for, and provides an immutable
    historical record even if the ``GateEvent`` is later auto-closed or exited.

    Denormalized ``person``, ``visitor_category``, ``entered_at``, ``gate``, and
    ``duration_minutes_at_handover`` fields mean the "handover receipt" can be
    rendered without joining to ``GateEvent`` (which may later transition to
    ``EXITED``/``AUTO_CLOSED``). This matches the snapshot philosophy: a handover
    captures the state of the world *at handover time*.

    No ``is_active``/``deleted_at`` — items are immutable snapshots. They are
    deleted only if the parent ``ShiftHandover`` is hard-deleted (CASCADE).
    """

    society = models.ForeignKey(
        "housing.Society",
        on_delete=models.CASCADE,
        related_name="shift_handover_items",
        verbose_name=_("society"),
    )
    handover = models.ForeignKey(
        "gateops.ShiftHandover",
        on_delete=models.CASCADE,
        related_name="items",
        verbose_name=_("handover"),
    )
    gate_event = models.ForeignKey(
        "gateops.GateEvent",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="handover_items",
        verbose_name=_("gate event"),
    )
    person = models.ForeignKey(
        "gateops.Person",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="handover_items",
        verbose_name=_("person"),
    )
    visitor_category = models.ForeignKey(
        "gateops.VisitorCategory",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="handover_items",
        verbose_name=_("visitor category"),
    )
    entered_at = models.DateTimeField(_("entered at"), null=True, blank=True)
    duration_minutes_at_handover = models.PositiveIntegerField(
        _("duration minutes at handover"), default=0
    )
    gate = models.ForeignKey(
        "gateops.Gate",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="handover_items",
        verbose_name=_("gate"),
    )
    is_overstay = models.BooleanField(_("is overstay"), default=False)
    notes = models.TextField(_("notes"), blank=True)
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)

    class Meta:
        verbose_name = _("Shift Handover Item")
        verbose_name_plural = _("Shift Handover Items")
        ordering = ("handover", "-entered_at")
        indexes = [
            models.Index(fields=["handover"], name="hitem_handover_idx"),
            models.Index(fields=["society", "person"], name="hitem_soc_person_idx"),
            models.Index(fields=["society", "gate_event"], name="hitem_soc_event_idx"),
        ]
        constraints = [
            # One item per handover per gate event (a person inside is listed once).
            models.UniqueConstraint(
                fields=["handover", "gate_event"],
                name="uniq_handover_item_per_event",
                condition=models.Q(gate_event__isnull=False),
            ),
        ]

    def __str__(self):
        person = self.person.name if self.person else "Unknown"
        return f"{person} inside {self.duration_minutes_at_handover}min @ {self.handover}"

    def clean(self):
        super().clean()
        if self.handover_id is not None and self.handover.society_id != self.society_id:
            raise ValidationError(
                {"society": _("Society must match the handover's society.")}
            )
        if (
            self.gate_event_id is not None
            and self.gate_event.society_id != self.society_id
        ):
            raise ValidationError(
                {"gate_event": _("Gate event must belong to the same society.")}
            )
        if self.person_id is not None and self.person.society_id != self.society_id:
            raise ValidationError(
                {"person": _("Person must belong to the same society.")}
            )
        if (
            self.visitor_category_id is not None
            and self.visitor_category.society_id != self.society_id
        ):
            raise ValidationError(
                {"visitor_category": _("Visitor category must belong to the same society.")}
            )
        if self.gate_id is not None and self.gate.society_id != self.society_id:
            raise ValidationError(
                {"gate": _("Gate must belong to the same society.")}
            )

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)
