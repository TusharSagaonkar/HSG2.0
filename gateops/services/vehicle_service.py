"""Service layer for the ``GateVehicle`` model (Phase 6 — Vehicle Module).

This service is the single authority over :class:`GateVehicle` lookups,
registration, watchlisting, and ANPR-ready hooks. No caller should mutate a
``GateVehicle``'s watchlist/repeat flags directly — every state-changing
operation must flow through :class:`VehicleService` so that:

1. Multi-tenant safety is enforced (every query scoped by ``society``).
2. The *before* state is captured as JSON.
3. The transition is applied (race-safe via ``update()``).
4. The *after* state is captured as JSON.
5. A :class:`GateOpsAuditLog` entry is written (append-only).

Design notes
------------
- **Multi-tenant safety:** every query is scoped by ``society``. A vehicle
  registered in one society can never be looked up or mutated from another
  society's context. Registration additionally asserts
  ``person.society == vehicle_category.society == society``.
- **Race safety:** watchlist/repeat/last_seen updates use ``QuerySet.update()``
  (not ``save()``) so concurrent gate scans of the same vehicle cannot lose
  updates or interleave transitions.
- **Pure lookups:** ``lookup`` and ``anpr_lookup`` are read-only and have no
  side effects (no audit logging, no ``last_seen_at`` mutation). The
  ``last_seen_at`` update happens only in ``register_or_create``.
- **Audit robustness:** audit-log writes are wrapped so a logging failure never
  blocks a legitimate vehicle operation (the error is logged loudly instead).
- **All methods are ``@staticmethod``** per the service contract; there is no
  shared mutable state.
"""

from __future__ import annotations

import logging

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, QuerySet
from django.utils import timezone

from gateops.models import (
    GateOpsAuditLog,
    GateVehicle,
    Person,
    VehicleCategory,
)

logger = logging.getLogger(__name__)


class VehicleService:
    """Service for GateVehicle lookup, registration, watchlisting, and ANPR.

    Every state-changing operation:
    1. Validates multi-tenant safety (society scoping).
    2. Captures before state as JSON.
    3. Applies the transition (race-safe via ``update()``).
    4. Captures after state as JSON.
    5. Creates a GateOpsAuditLog entry.
    """

    # ------------------------------------------------------------------ #
    # Public read API
    # ------------------------------------------------------------------ #

    @staticmethod
    def lookup(*, society, vehicle_number):
        """Look up an active GateVehicle by ``(society, vehicle_number)``.

        Pure read: normalizes the plate, fetches the active vehicle, and
        returns it (or ``None`` if not found). Does NOT mutate ``last_seen_at``
        and does NOT audit — use :meth:`register_or_create` for the
        side-effecting path.
        """
        normalized = VehicleService._normalize_plate(vehicle_number)
        return GateVehicle.objects.filter(
            society=society,
            vehicle_number=normalized,
            is_active=True,
        ).first()

    @staticmethod
    def get_watchlisted(*, society) -> QuerySet:
        """Return all active watchlisted vehicles for a society.

        Ordered by most-recently-updated first. Uses ``select_related`` on
        ``person`` and ``vehicle_category`` to avoid N+1 on serialization.
        """
        return (
            GateVehicle.objects.filter(
                society=society,
                is_watchlisted=True,
                is_active=True,
            )
            .select_related("person", "vehicle_category")
            .order_by("-updated_at")
        )

    @staticmethod
    def get_recent(*, society, limit=50) -> QuerySet:
        """Return the most recently seen active vehicles for a society.

        ``limit`` caps the result set (default 50). Ordered by
        ``last_seen_at`` descending; vehicles never seen sort last.
        """
        return (
            GateVehicle.objects.filter(society=society, is_active=True)
            .select_related("person", "vehicle_category")
            .order_by("-last_seen_at")[:limit]
        )

    @staticmethod
    def search(*, society, query) -> QuerySet:
        """Search active vehicles by plate, person name, or person phone.

        Case-insensitive ``icontains`` matches across the three indexed access
        paths. Returns a ``select_related`` queryset to avoid N+1 on display.
        """
        return (
            GateVehicle.objects.filter(society=society, is_active=True)
            .filter(
                Q(vehicle_number__icontains=query)
                | Q(person__name__icontains=query)
                | Q(person__phone__icontains=query)
            )
            .select_related("person", "vehicle_category")
        )

    @staticmethod
    def anpr_lookup(*, society, plate_text) -> dict:
        """ANPR-ready hook (Phase 15 integration point).

        Pure read with no side effects: normalizes the plate, looks up the
        vehicle, and returns a JSON-safe dict describing the match. This is
        the contract an ANPR camera feed will call on each plate capture.
        """
        logger.debug(
            "ANPR lookup for plate '%s' in society %s", plate_text, society.pk
        )
        normalized = VehicleService._normalize_plate(plate_text)
        vehicle = GateVehicle.objects.filter(
            society=society,
            vehicle_number=normalized,
            is_active=True,
        ).first()
        if vehicle is None:
            return {
                "found": False,
                "vehicle": None,
                "watchlisted": False,
                "category_code": None,
            }
        return {
            "found": True,
            "vehicle": vehicle,
            "watchlisted": vehicle.is_currently_watchlisted,
            "category_code": vehicle.vehicle_category.code,
        }

    # ------------------------------------------------------------------ #
    # Public state-changing API
    # ------------------------------------------------------------------ #

    @staticmethod
    @transaction.atomic
    def register_or_create(
        *,
        society,
        vehicle_number,
        person,
        vehicle_category,
        is_repeat=None,
        notes="",
        actor=None,
    ) -> GateVehicle:
        """Register a vehicle sighting, creating it if unseen.

        - Normalizes the plate (uppercase, strip).
        - Validates multi-tenant safety: ``person.society`` and
          ``vehicle_category.society`` must equal ``society``.
        - If the vehicle exists (active): updates ``last_seen_at``, refreshes
          ``person`` if different, sets ``is_repeat=True`` (it is a repeat
          visit), and audits an UPDATE.
        - If not found: creates a new GateVehicle with ``is_repeat=False`` and
          ``last_seen_at=now``, and audits a CREATE.

        Returns the (created or updated) vehicle.
        """
        # Multi-tenant safety: person and category must belong to this society.
        if person.society_id != society.pk:
            raise ValidationError(
                "Person must belong to the same society as the vehicle."
            )
        if vehicle_category.society_id != society.pk:
            raise ValidationError(
                "Vehicle category must belong to the same society as the vehicle."
            )

        normalized = VehicleService._normalize_plate(vehicle_number)
        now = timezone.now()

        existing = GateVehicle.objects.filter(
            society=society,
            vehicle_number=normalized,
            is_active=True,
        ).first()

        if existing is not None:
            before = VehicleService._serialize(existing)
            # Race-safe update: a targeted UPDATE avoids clobbering concurrent
            # watchlist/repeat transitions on the same vehicle.
            update_fields = {"last_seen_at": now, "is_repeat": True}
            if existing.person_id != person.pk:
                update_fields["person"] = person
            if notes:
                update_fields["notes"] = notes
            GateVehicle.objects.filter(pk=existing.pk).update(**update_fields)
            existing.refresh_from_db()
            after = VehicleService._serialize(existing)
            VehicleService._log_audit(
                society=society,
                action=GateOpsAuditLog.Action.UPDATE,
                gate_vehicle=existing,
                before=before,
                after=after,
                actor=actor,
            )
            return existing

        # New vehicle sighting — first time this plate is logged.
        vehicle = GateVehicle(
            society=society,
            person=person,
            vehicle_number=normalized,
            vehicle_category=vehicle_category,
            is_repeat=False,
            last_seen_at=now,
            notes=notes,
        )
        vehicle.save()
        after = VehicleService._serialize(vehicle)
        VehicleService._log_audit(
            society=society,
            action=GateOpsAuditLog.Action.CREATE,
            gate_vehicle=vehicle,
            before=None,
            after=after,
            actor=actor,
        )
        return vehicle

    @staticmethod
    @transaction.atomic
    def add_to_watchlist(*, gate_vehicle, reason, actor=None) -> GateVehicle:
        """Flag a vehicle as watchlisted with a mandatory reason.

        Raises ``ValidationError`` if the vehicle is already watchlisted or if
        ``reason`` is empty. Uses ``update()`` for race safety and audits a
        STATE_TRANSITION with before/after state.
        """
        if not reason or not reason.strip():
            raise ValidationError("Watchlist reason is required.")
        if gate_vehicle.is_watchlisted:
            raise ValidationError("Vehicle is already watchlisted")

        before = VehicleService._serialize(gate_vehicle)
        GateVehicle.objects.filter(pk=gate_vehicle.pk).update(
            is_watchlisted=True,
            watchlist_reason=reason,
        )
        gate_vehicle.refresh_from_db()
        after = VehicleService._serialize(gate_vehicle)
        VehicleService._log_audit(
            society=gate_vehicle.society,
            action=GateOpsAuditLog.Action.STATE_TRANSITION,
            gate_vehicle=gate_vehicle,
            before=before,
            after=after,
            actor=actor,
        )
        return gate_vehicle

    @staticmethod
    @transaction.atomic
    def remove_from_watchlist(*, gate_vehicle, actor=None, reason="") -> GateVehicle:
        """Clear the watchlist flag on a vehicle.

        Raises ``ValidationError`` if the vehicle is not currently watchlisted.
        Clears ``watchlist_reason``. Uses ``update()`` for race safety and
        audits a STATE_TRANSITION with before/after state.
        """
        if not gate_vehicle.is_watchlisted:
            raise ValidationError("Vehicle is not watchlisted")

        before = VehicleService._serialize(gate_vehicle)
        GateVehicle.objects.filter(pk=gate_vehicle.pk).update(
            is_watchlisted=False,
            watchlist_reason="",
        )
        gate_vehicle.refresh_from_db()
        after = VehicleService._serialize(gate_vehicle)
        if reason:
            after["reason"] = reason
        VehicleService._log_audit(
            society=gate_vehicle.society,
            action=GateOpsAuditLog.Action.STATE_TRANSITION,
            gate_vehicle=gate_vehicle,
            before=before,
            after=after,
            actor=actor,
        )
        return gate_vehicle

    @staticmethod
    @transaction.atomic
    def mark_repeat(*, society, vehicle_number, actor=None):
        """Mark a vehicle as a repeat visitor (idempotent).

        Looks up the vehicle by ``(society, vehicle_number)``; returns ``None``
        if not found. If already flagged ``is_repeat``, returns as-is. Otherwise
        sets ``is_repeat=True`` via ``update()`` and audits an UPDATE.
        """
        vehicle = VehicleService.lookup(society=society, vehicle_number=vehicle_number)
        if vehicle is None:
            return None
        if vehicle.is_repeat:
            return vehicle

        before = VehicleService._serialize(vehicle)
        GateVehicle.objects.filter(pk=vehicle.pk).update(is_repeat=True)
        vehicle.refresh_from_db()
        after = VehicleService._serialize(vehicle)
        VehicleService._log_audit(
            society=society,
            action=GateOpsAuditLog.Action.UPDATE,
            gate_vehicle=vehicle,
            before=before,
            after=after,
            actor=actor,
        )
        return vehicle

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _normalize_plate(number) -> str:
        """Normalize a plate string: uppercase and strip whitespace.

        This matches the normalization applied in
        :meth:`GateVehicle.clean` so lookups hit the same stored form.
        """
        return (number or "").upper().strip()

    @staticmethod
    def _serialize(gate_vehicle) -> dict:
        """Return a JSON-safe dict of the vehicle's key fields for audit logging."""
        return {
            "id": str(gate_vehicle.pk),
            "vehicle_number": gate_vehicle.vehicle_number,
            "is_watchlisted": gate_vehicle.is_watchlisted,
            "is_repeat": gate_vehicle.is_repeat,
            "vehicle_category": gate_vehicle.vehicle_category.code,
            "person": gate_vehicle.person.name,
            "last_seen_at": (
                gate_vehicle.last_seen_at.isoformat()
                if gate_vehicle.last_seen_at
                else None
            ),
        }

    @staticmethod
    def _log_audit(
        *,
        society,
        action,
        gate_vehicle,
        before=None,
        after=None,
        actor=None,
    ) -> None:
        """Write an append-only GateOpsAuditLog entry for a vehicle operation.

        Wrapped so a logging failure never blocks a legitimate vehicle
        operation; the error is logged at ERROR level instead.
        """
        try:
            GateOpsAuditLog.log(
                society=society,
                action=action,
                entity_type="GateVehicle",
                entity_id=str(gate_vehicle.pk),
                actor=actor,
                before_value=before,
                after_value=after,
            )
        except Exception:  # noqa: BLE001 — audit must not break the operation.
            logger.exception(
                "Failed to write GateVehicle audit log for vehicle %s (action=%s)",
                gate_vehicle.pk,
                action,
            )
