from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _
from django.db.models import Q


class GateVehicle(models.Model):
    """Gate-side vehicle record for visitor/non-resident vehicles.

    Phase 6 — Vehicle Module. Distinct from ``parking.Vehicle`` which holds
    resident-owned vehicles. Linked to ``GateEvent`` via ``gate_vehicle`` FK.

    A single physical visitor vehicle maps to exactly one ``GateVehicle`` row
    per society, identified by ``(society, vehicle_number)``. This enables
    repeat-visitor detection, watchlisting, and ANPR integration without
    duplicating vehicle data across every gate event.

    The ``vehicle_number`` is normalized to uppercase on save so lookups are
    case-insensitive at the storage layer. Uniqueness is enforced per society
    among active vehicles only (a soft-deleted number may be reused).

    Soft-delete follows the established ``is_active`` + ``deleted_at`` pattern.
    """

    society = models.ForeignKey(
        "housing.Society",
        on_delete=models.CASCADE,
        related_name="gate_vehicles",
        verbose_name=_("society"),
    )
    person = models.ForeignKey(
        "gateops.Person",
        on_delete=models.PROTECT,
        related_name="gate_vehicles",
        verbose_name=_("person"),
    )
    vehicle_number = models.CharField(
        _("vehicle number"), max_length=30, db_index=True,
    )
    vehicle_category = models.ForeignKey(
        "gateops.VehicleCategory",
        on_delete=models.PROTECT,
        related_name="gate_vehicles",
        verbose_name=_("vehicle category"),
    )
    is_watchlisted = models.BooleanField(
        _("watchlisted"), default=False, db_index=True,
    )
    watchlist_reason = models.TextField(_("watchlist reason"), blank=True)
    is_repeat = models.BooleanField(
        _("repeat visitor"), default=False, db_index=True,
    )
    first_seen_at = models.DateTimeField(_("first seen at"), auto_now_add=True)
    last_seen_at = models.DateTimeField(
        _("last seen at"), null=True, blank=True, db_index=True,
    )
    notes = models.TextField(_("notes"), blank=True)
    is_active = models.BooleanField(_("active"), default=True)
    deleted_at = models.DateTimeField(_("deleted at"), null=True, blank=True)
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)

    class Meta:
        verbose_name = _("Gate Vehicle")
        verbose_name_plural = _("Gate Vehicles")
        ordering = ("society", "-last_seen_at")
        constraints = [
            models.UniqueConstraint(
                fields=["society", "vehicle_number"],
                condition=Q(is_active=True),
                name="uniq_gate_vehicle_number_per_society",
            ),
        ]
        indexes = [
            models.Index(fields=["society", "is_watchlisted"], name="gveh_soc_watchlist_idx"),
            models.Index(fields=["society", "vehicle_category"], name="gveh_soc_category_idx"),
            models.Index(fields=["society", "person"], name="gveh_soc_person_idx"),
        ]

    def __str__(self):
        return f"{self.vehicle_number} ({self.vehicle_category.code})"

    @property
    def is_currently_watchlisted(self):
        """Return ``True`` only when the vehicle is both watchlisted and active.

        A soft-deleted vehicle is never considered a current security concern,
        even if ``is_watchlisted`` was True before deletion.
        """
        return self.is_watchlisted and self.is_active

    def clean(self):
        super().clean()
        # vehicle_number is mandatory and must not be blank/whitespace.
        if not self.vehicle_number or not self.vehicle_number.strip():
            raise ValidationError({"vehicle_number": _("Vehicle number is required.")})
        # Normalize to uppercase and strip whitespace so lookups are
        # case-insensitive at the storage layer (callers passing lowercase
        # plates are normalized rather than rejected).
        self.vehicle_number = self.vehicle_number.upper().strip()
        # Watchlisting requires a reason; an unexplained watchlist is an audit gap.
        if self.is_watchlisted and not self.watchlist_reason.strip():
            raise ValidationError(
                {"watchlist_reason": _("Watchlist reason is required when watchlisted.")}
            )

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)
