import decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class Parcel(models.Model):
    """Parcel received at the gate and held for resident collection.

    Phase 8 — Parcel Management. Tracks courier-delivered parcels that arrive
    at the society gate and are stored until the resident collects them with an
    OTP. A gate event can have multiple parcels (line items).

    ``Parcel`` is a *child* entity of :class:`GateEvent` — the FK lives here,
    pointing to the parent event (mirroring the
    :class:`MaterialMovement` / :class:`GateEventDocument` pattern). Phase 8
    therefore does NOT alter the ``GateEvent`` schema; it extends the event
    behaviorally.

    The ``society`` FK is denormalized from the parent ``GateEvent`` so that
    tenant-scoped queries (pending collections, overdue sweeps) do not require
    a join to ``GateEvent`` — matching the ``MaterialMovement`` /
    ``GateEventDocument`` denormalization pattern.

    Soft-delete follows the established ``is_active`` + ``deleted_at``
    pattern. The ``status`` field drives a small state machine
    (``RECEIVED`` → ``COLLECTED`` / ``RETURNED`` / ``LOST``) that is enforced
    by :class:`gateops.services.parcel_service.ParcelService`.

    Cash-on-delivery (COD) parcels carry a ``cod_amount``; the amount is only
    meaningful when ``is_cod`` is ``True`` (``clean()`` clears it otherwise).
    An ``otp_code`` is generated at receipt time and verified at collection to
    prevent mis-delivery.
    """

    class Status(models.TextChoices):
        RECEIVED = "received", _("Received")
        COLLECTED = "collected", _("Collected")
        RETURNED = "returned", _("Returned")
        LOST = "lost", _("Lost")

    society = models.ForeignKey(
        "housing.Society",
        on_delete=models.CASCADE,
        related_name="parcels",
        verbose_name=_("society"),
    )
    gate_event = models.ForeignKey(
        "gateops.GateEvent",
        on_delete=models.CASCADE,
        related_name="parcels",
        verbose_name=_("gate event"),
    )
    tracking_number = models.CharField(
        _("tracking number"), max_length=100,
    )
    courier = models.CharField(
        _("courier"), max_length=100, blank=True,
    )
    is_cold_storage = models.BooleanField(
        _("cold storage"), default=False,
    )
    is_fragile = models.BooleanField(
        _("fragile"), default=False,
    )
    is_cod = models.BooleanField(
        _("cash on delivery"), default=False,
    )
    cod_amount = models.DecimalField(
        _("COD amount"),
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
    )
    otp_code = models.CharField(
        _("OTP code"), max_length=10, blank=True,
    )
    status = models.CharField(
        _("status"),
        max_length=20,
        choices=Status.choices,
        default=Status.RECEIVED,
        db_index=True,
    )
    stored_at = models.DateTimeField(
        _("stored at"), null=True, blank=True,
    )
    collected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="collected_parcels",
        verbose_name=_("collected by"),
    )
    collected_at = models.DateTimeField(
        _("collected at"), null=True, blank=True,
    )
    is_active = models.BooleanField(_("active"), default=True)
    deleted_at = models.DateTimeField(_("deleted at"), null=True, blank=True)
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)

    class Meta:
        verbose_name = _("Parcel")
        verbose_name_plural = _("Parcels")
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["society", "status"], name="parcel_soc_status_idx",
            ),
            models.Index(
                fields=["society", "gate_event"], name="parcel_soc_event_idx",
            ),
            models.Index(
                fields=["society", "tracking_number"], name="parcel_soc_tracking_idx",
            ),
            models.Index(
                fields=["society", "stored_at"], name="parcel_soc_stored_idx",
            ),
        ]

    def __str__(self):
        return f"Parcel {self.tracking_number} ({self.get_status_display()})"

    # ------------------------------------------------------------------ #
    # Convenience properties
    # ------------------------------------------------------------------ #

    @property
    def is_pending(self):
        """Return ``True`` when the parcel is still awaiting collection."""
        return self.status == self.Status.RECEIVED

    @property
    def is_collected(self):
        """Return ``True`` when the parcel has reached the terminal COLLECTED state."""
        return self.status == self.Status.COLLECTED

    @property
    def is_terminal(self):
        """Return ``True`` when the parcel is in a terminal state.

        Terminal states (``COLLECTED``, ``RETURNED``, ``LOST``) have no
        outgoing transitions in the state machine.
        """
        return self.status in {
            self.Status.COLLECTED,
            self.Status.RETURNED,
            self.Status.LOST,
        }

    # ------------------------------------------------------------------ #
    # Validation
    # ------------------------------------------------------------------ #

    def clean(self):
        super().clean()
        # tracking_number is mandatory; a blank value makes the parcel
        # untraceable and breaks the (society, tracking_number) lookup path.
        if not self.tracking_number or not self.tracking_number.strip():
            raise ValidationError(
                {"tracking_number": _("Tracking number is required.")}
            )

        # COD consistency: a COD parcel must carry a positive amount, and a
        # non-COD parcel must never carry an amount (clear it so the column
        # stays a reliable indicator of money owed at the gate).
        if self.is_cod:
            if self.cod_amount is None or self.cod_amount <= 0:
                raise ValidationError(
                    {"cod_amount": _("COD amount must be greater than zero for COD parcels.")}
                )
        else:
            # Clear any stale amount so non-COD parcels never report money.
            if self.cod_amount is not None:
                self.cod_amount = None

        # Collection consistency: a COLLECTED parcel must record when it was
        # collected. Keeping collected_at in sync with status is what makes
        # the pending-collection queries trustworthy.
        if self.status == self.Status.COLLECTED and self.collected_at is None:
            raise ValidationError(
                {"collected_at": _("collected_at must be set when status is COLLECTED.")}
            )

        # A non-COLLECTED parcel must not retain a collector — clearing
        # collected_by keeps the audit trail honest (a returned/lost parcel
        # was never collected by anyone).
        if self.status != self.Status.COLLECTED and self.collected_by is not None:
            self.collected_by = None

    def save(self, *args, **kwargs):
        # Model-level validation runs on every save() (single-row writes).
        # Bulk operations (update()/bulk_create) bypass save() and therefore
        # bypass clean() — the service layer is responsible for validating
        # those paths.
        self.clean()
        super().save(*args, **kwargs)
