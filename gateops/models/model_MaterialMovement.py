from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class MaterialMovement(models.Model):
    """Material movement record linked to a gate event.

    Phase 7 — Material Movement. Tracks materials entering or leaving the
    society premises (e.g., construction materials, furniture, appliances).
    A gate event can have multiple material movements (line items).

    ``MaterialMovement`` is a *child* entity of :class:`GateEvent` — the FK
    lives here, pointing to the parent event (mirroring the
    :class:`GateEventPhoto` / :class:`GateEventDocument` pattern). Phase 7
    therefore does NOT alter the ``GateEvent`` schema; it extends the event
    behaviorally via ``GateEventDocument`` (gate passes) and
    ``GateEventPhoto`` (material photos, using existing
    ``PhotoType.MATERIAL``).

    The ``society`` FK is denormalized from the parent ``GateEvent`` so that
    tenant-scoped queries (pending returns, overdue sweeps) do not require a
    join to ``GateEvent`` — matching the ``GateEventDocument`` /
    ``GateEventPhoto`` denormalization pattern.

    Soft-delete follows the established ``is_active`` + ``deleted_at``
    pattern. The ``status`` field drives a small state machine
    (``IN_TRANSIT`` → ``RETURNED`` / ``OVERDUE`` / ``CANCELLED``) that is
    enforced by :class:`gateops.services.material_service.MaterialService`.
    """

    class Status(models.TextChoices):
        IN_TRANSIT = "in_transit", _("In Transit")
        RETURNED = "returned", _("Returned")
        OVERDUE = "overdue", _("Overdue")
        CANCELLED = "cancelled", _("Cancelled")

    society = models.ForeignKey(
        "housing.Society",
        on_delete=models.CASCADE,
        related_name="material_movements",
        verbose_name=_("society"),
    )
    gate_event = models.ForeignKey(
        "gateops.GateEvent",
        on_delete=models.CASCADE,
        related_name="material_movements",
        verbose_name=_("gate event"),
    )
    material_category = models.ForeignKey(
        "gateops.MaterialCategory",
        on_delete=models.PROTECT,
        related_name="material_movements",
        verbose_name=_("material category"),
    )
    quantity = models.DecimalField(
        _("quantity"),
        max_digits=10,
        decimal_places=2,
        default=1,
    )
    unit = models.CharField(
        _("unit"), max_length=20, default="unit",
    )
    owner = models.CharField(
        _("owner"), max_length=200, blank=True,
    )
    purpose = models.TextField(_("purpose"), blank=True)
    expected_return_at = models.DateTimeField(
        _("expected return at"), null=True, blank=True,
    )
    returned_at = models.DateTimeField(
        _("returned at"), null=True, blank=True,
    )
    signature_image = models.ImageField(
        _("signature"),
        null=True,
        blank=True,
        upload_to="gateops/materials/signatures/",
    )
    status = models.CharField(
        _("status"),
        max_length=20,
        choices=Status.choices,
        default=Status.IN_TRANSIT,
        db_index=True,
    )
    is_active = models.BooleanField(_("active"), default=True)
    deleted_at = models.DateTimeField(_("deleted at"), null=True, blank=True)
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)

    class Meta:
        verbose_name = _("Material Movement")
        verbose_name_plural = _("Material Movements")
        ordering = ("society", "-created_at")
        indexes = [
            models.Index(
                fields=["society", "status"], name="matmov_soc_status_idx",
            ),
            models.Index(
                fields=["society", "material_category"], name="matmov_soc_cat_idx",
            ),
            models.Index(
                fields=["society", "gate_event"], name="matmov_soc_event_idx",
            ),
            models.Index(
                fields=["society", "expected_return_at"], name="matmov_soc_return_idx",
            ),
        ]

    def __str__(self):
        return f"{self.quantity} {self.unit} ({self.material_category.code})"

    @property
    def is_overdue(self):
        """Return ``True`` when an in-transit movement has passed its expected return time.

        A movement that has already been returned or cancelled is never
        considered overdue, even if ``expected_return_at`` is in the past.
        """
        return (
            self.status == self.Status.IN_TRANSIT
            and self.expected_return_at is not None
            and self.expected_return_at < timezone.now()
        )

    @property
    def is_returned(self):
        """Return ``True`` when the movement has reached the terminal RETURNED state."""
        return self.status == self.Status.RETURNED

    def clean(self):
        super().clean()
        # quantity must be strictly positive — a zero/negative movement is
        # nonsensical and would corrupt return-tracking arithmetic.
        if self.quantity is None or self.quantity <= 0:
            raise ValidationError({"quantity": _("Quantity must be greater than zero.")})
        # unit is mandatory; the default "unit" is a fallback, not a free pass.
        if not self.unit or not self.unit.strip():
            raise ValidationError({"unit": _("Unit is required.")})
        # If a return timestamp is recorded, the status must reflect it.
        # Keeping the status in sync with returned_at is what makes the
        # pending-returns / overdue queries trustworthy.
        if self.returned_at is not None and self.status != self.Status.RETURNED:
            raise ValidationError(
                {"status": _("Status must be RETURNED when returned_at is set.")}
            )

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)
