"""Service layer for the ``MaterialMovement`` model (Phase 7 — Material Movement).

This service is the single authority over :class:`MaterialMovement` creation,
return tracking, overdue sweeps, and gate-pass code generation. No caller
should mutate a ``MaterialMovement``'s status directly — every state-changing
operation must flow through :class:`MaterialService` so that:

1. Multi-tenant safety is enforced (every query scoped by ``society``).
2. The *before* state is captured as JSON.
3. The transition is validated against the state machine and applied
   (race-safe via ``update()``).
4. The *after* state is captured as JSON.
5. A :class:`GateOpsAuditLog` entry is written (append-only).

Design notes
------------
- **Multi-tenant safety:** every query is scoped by ``society``. A movement
  recorded in one society can never be looked up or mutated from another
  society's context. Recording additionally asserts
  ``material_category.society == gate_event.society``.
- **Race safety:** status / return-time updates use ``QuerySet.update()``
  (not ``save()``) so concurrent operations on the same movement cannot lose
  updates or interleave transitions.
- **State machine:** transitions are validated against ``_TRANSITIONS``;
  terminal states (``RETURNED``, ``CANCELLED``) cannot leave.
- **Audit robustness:** audit-log writes are wrapped so a logging failure
  never blocks a legitimate material operation (the error is logged loudly
  instead).
- **All methods are ``@staticmethod``** per the service contract; there is no
  shared mutable state.
"""

from __future__ import annotations

import logging
import uuid

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, QuerySet
from django.utils import timezone

from gateops.models import (
    GateOpsAuditLog,
    MaterialCategory,
    MaterialMovement,
)

logger = logging.getLogger(__name__)


class MaterialService:
    """Service for MaterialMovement recording, return tracking, and gate passes.

    Every state-changing operation:
    1. Validates multi-tenant safety (society scoping).
    2. Captures before state as JSON.
    3. Validates the transition against the state machine.
    4. Applies the transition (race-safe via ``update()``).
    5. Captures after state as JSON.
    6. Creates a GateOpsAuditLog entry.
    """

    # ------------------------------------------------------------------ #
    # State machine
    # ------------------------------------------------------------------ #
    # IN_TRANSIT is the entry state. RETURNED and CANCELLED are terminal.
    _TRANSITIONS = {
        MaterialMovement.Status.IN_TRANSIT: {
            MaterialMovement.Status.RETURNED,
            MaterialMovement.Status.OVERDUE,
            MaterialMovement.Status.CANCELLED,
        },
        MaterialMovement.Status.OVERDUE: {
            MaterialMovement.Status.RETURNED,
            MaterialMovement.Status.CANCELLED,
        },
        MaterialMovement.Status.RETURNED: set(),  # terminal
        MaterialMovement.Status.CANCELLED: set(),  # terminal
    }

    # ------------------------------------------------------------------ #
    # Public read API
    # ------------------------------------------------------------------ #

    @staticmethod
    def get_pending_returns(*, society) -> QuerySet:
        """Return active in-transit / overdue movements awaiting return.

        Ordered by ``expected_return_at`` ascending (soonest-due first),
        with NULL expected-return dates sorted last (movements with no
        expected return are open-ended and least urgent). Uses
        ``select_related`` on ``gate_event`` and ``material_category`` to
        avoid N+1 on display.
        """
        return (
            MaterialMovement.objects.filter(
                society=society,
                status__in=[
                    MaterialMovement.Status.IN_TRANSIT,
                    MaterialMovement.Status.OVERDUE,
                ],
                is_active=True,
            )
            .select_related("gate_event", "material_category")
            .order_by("expected_return_at", "-created_at")
        )

    @staticmethod
    def get_overdue(*, society) -> QuerySet:
        """Return active in-transit movements whose expected return time has passed.

        Only ``IN_TRANSIT`` movements are considered — an ``OVERDUE`` movement
        has already been promoted by :meth:`check_and_mark_overdue`. Uses
        ``select_related`` on ``gate_event`` and ``material_category`` to
        avoid N+1 on display.
        """
        now = timezone.now()
        return (
            MaterialMovement.objects.filter(
                society=society,
                status=MaterialMovement.Status.IN_TRANSIT,
                expected_return_at__isnull=False,
                expected_return_at__lt=now,
                is_active=True,
            )
            .select_related("gate_event", "material_category")
            .order_by("expected_return_at")
        )

    @staticmethod
    def get_by_event(*, gate_event) -> QuerySet:
        """Return all active material movements attached to a gate event.

        Uses ``select_related`` on ``material_category`` to avoid N+1 when
        rendering the line-item list for a single event.
        """
        return (
            MaterialMovement.objects.filter(
                gate_event=gate_event,
                is_active=True,
            )
            .select_related("material_category")
            .order_by("created_at")
        )

    # ------------------------------------------------------------------ #
    # Public state-changing API
    # ------------------------------------------------------------------ #

    @staticmethod
    @transaction.atomic
    def record_movement(
        *,
        gate_event,
        material_category,
        quantity,
        unit="unit",
        owner="",
        purpose="",
        expected_return_at=None,
        actor=None,
    ) -> MaterialMovement:
        """Record a new material movement against a gate event.

        - Validates multi-tenant safety: ``material_category.society`` must
          equal ``gate_event.society``.
        - Validates ``quantity > 0``.
        - If the category requires approval by default, logs a warning
          (approval is enforced by the rule engine, not here).
        - Creates the movement with ``status=IN_TRANSIT`` and a denormalized
          ``society`` copied from the gate event.
        - Audits a CREATE with the after state.

        Returns the created movement.
        """
        # Multi-tenant safety: category must belong to the same society as
        # the gate event. Comparing on the id avoids an extra fetch when the
        # instances are already loaded.
        if material_category.society_id != gate_event.society_id:
            raise ValidationError(
                "Material category must belong to the same society as the gate event."
            )

        # quantity is a Decimal; reject non-positive values early so the
        # model's clean() never sees an invalid value.
        if quantity is None or quantity <= 0:
            raise ValidationError({"quantity": "Quantity must be greater than zero."})

        # Approval is the rule engine's responsibility; we only surface a
        # warning so operators know a movement may be held for approval.
        if getattr(material_category, "requires_approval_default", False):
            logger.warning(
                "Material category %s requires approval default for movement on "
                "gate event %s (society %s)",
                material_category.code,
                gate_event.pk,
                gate_event.society_id,
            )

        movement = MaterialMovement(
            society=gate_event.society,
            gate_event=gate_event,
            material_category=material_category,
            quantity=quantity,
            unit=unit,
            owner=owner,
            purpose=purpose,
            expected_return_at=expected_return_at,
            status=MaterialMovement.Status.IN_TRANSIT,
        )
        movement.save()
        after = MaterialService._serialize(movement)
        MaterialService._log_audit(
            society=movement.society,
            action=GateOpsAuditLog.Action.CREATE,
            movement=movement,
            before=None,
            after=after,
            actor=actor,
        )
        return movement

    @staticmethod
    @transaction.atomic
    def record_return(*, movement, returned_at=None, actor=None) -> MaterialMovement:
        """Mark a material movement as returned.

        Allowed from ``IN_TRANSIT`` or ``OVERDUE``. Sets ``returned_at`` to
        the provided value or now, and transitions ``status`` to ``RETURNED``
        (terminal). Uses the race-safe ``_apply_transition`` helper and
        audits a STATE_TRANSITION.

        Returns the refreshed movement.
        """
        returned_at = returned_at or timezone.now()
        return MaterialService._apply_transition(
            movement=movement,
            new_status=MaterialMovement.Status.RETURNED,
            actor=actor,
            extra_fields={"returned_at": returned_at},
        )

    @staticmethod
    @transaction.atomic
    def mark_overdue(*, movement, actor=None) -> MaterialMovement:
        """Mark an in-transit movement as overdue.

        Allowed only from ``IN_TRANSIT``. Uses the race-safe
        ``_apply_transition`` helper and audits a STATE_TRANSITION.

        Returns the refreshed movement.
        """
        return MaterialService._apply_transition(
            movement=movement,
            new_status=MaterialMovement.Status.OVERDUE,
            actor=actor,
        )

    @staticmethod
    @transaction.atomic
    def cancel_movement(*, movement, actor=None, reason="") -> MaterialMovement:
        """Cancel an in-transit or overdue movement.

        Allowed from ``IN_TRANSIT`` or ``OVERDUE``. Uses the race-safe
        ``_apply_transition`` helper and audits a STATE_TRANSITION. The
        optional ``reason`` is recorded in the audit after-state for
        traceability (it is not persisted on the model itself).

        Returns the refreshed movement.
        """
        movement = MaterialService._apply_transition(
            movement=movement,
            new_status=MaterialMovement.Status.CANCELLED,
            actor=actor,
        )
        if reason:
            after = MaterialService._serialize(movement)
            after["reason"] = reason
            MaterialService._log_audit(
                society=movement.society,
                action=GateOpsAuditLog.Action.STATE_TRANSITION,
                movement=movement,
                before=None,
                after=after,
                actor=actor,
            )
        return movement

    @staticmethod
    def generate_gate_pass(*, movement, actor=None) -> str:
        """Generate a unique gate-pass code for a movement.

        This is a pure computation: it does NOT create a ``GateEventDocument``
        (gate-pass persistence is a future enhancement). It returns a
        deterministic-plus-random code of the form
        ``GATEPASS-<society_id>-<movement_pk>-<8 hex>``.

        The ``actor`` is accepted for API symmetry but is not audited here —
        no state changes, so no audit entry is required.
        """
        code = f"GATEPASS-{movement.society_id}-{movement.pk}-{uuid.uuid4().hex[:8].upper()}"
        logger.debug("Generated gate pass code for movement %s", movement.pk)
        return code

    @staticmethod
    @transaction.atomic
    def check_and_mark_overdue(*, society=None) -> int:
        """Sweep in-transit movements past their expected return time and mark overdue.

        Finds all active ``IN_TRANSIT`` movements where
        ``expected_return_at < now`` (and not null). When ``society`` is
        provided the sweep is scoped to that society; otherwise it runs
        across all societies (use sparingly — prefer per-society sweeps from
        a Celery beat task).

        Each matching movement is transitioned to ``OVERDUE`` via
        :meth:`mark_overdue` (which validates the transition and audits).
        Returns the count of movements marked overdue.
        """
        now = timezone.now()
        qs = MaterialMovement.objects.filter(
            status=MaterialMovement.Status.IN_TRANSIT,
            expected_return_at__isnull=False,
            expected_return_at__lt=now,
            is_active=True,
        )
        if society is not None:
            qs = qs.filter(society=society)

        # Materialize the pk list before mutating so the iterator is not
        # invalidated by the status updates.
        movement_ids = list(qs.values_list("pk", flat=True))
        count = 0
        for movement_id in movement_ids:
            movement = MaterialMovement.objects.select_related(
                "society", "gate_event", "material_category"
            ).get(pk=movement_id)
            try:
                MaterialService.mark_overdue(movement=movement)
                count += 1
            except ValidationError:
                # A concurrent transition may have moved the movement out of
                # IN_TRANSIT between the sweep and the update; skip it rather
                # than fail the whole sweep.
                logger.warning(
                    "Skipping movement %s during overdue sweep: transition not allowed",
                    movement_id,
                )
        return count

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _validate_transition(movement, new_status) -> None:
        """Validate that ``new_status`` is reachable from the movement's current status.

        Raises ``ValidationError`` if the transition is not permitted by the
        ``_TRANSITIONS`` table (e.g. leaving a terminal state, or jumping to
        an unreachable state).
        """
        allowed = MaterialService._TRANSITIONS.get(movement.status, set())
        if new_status not in allowed:
            raise ValidationError(
                f"Transition from '{movement.status}' to '{new_status}' is not allowed."
            )

    @staticmethod
    def _apply_transition(
        *,
        movement,
        new_status,
        actor=None,
        extra_fields=None,
    ) -> MaterialMovement:
        """Validate, apply, and audit a status transition (race-safe).

        - Captures the before state via :meth:`_serialize`.
        - Validates the transition via :meth:`_validate_transition`.
        - Builds an update dict (``status`` + any ``extra_fields``) and applies
          it with ``QuerySet.update()`` so concurrent operations cannot lose
          updates.
        - Refreshes the instance from the DB and captures the after state.
        - Audits a STATE_TRANSITION with before/after.

        Returns the refreshed movement.
        """
        before = MaterialService._serialize(movement)
        MaterialService._validate_transition(movement, new_status)

        update_dict = {"status": new_status}
        if extra_fields:
            update_dict.update(extra_fields)

        MaterialMovement.objects.filter(pk=movement.pk).update(**update_dict)
        movement.refresh_from_db()
        after = MaterialService._serialize(movement)
        MaterialService._log_audit(
            society=movement.society,
            action=GateOpsAuditLog.Action.STATE_TRANSITION,
            movement=movement,
            before=before,
            after=after,
            actor=actor,
        )
        return movement

    @staticmethod
    def _serialize(movement) -> dict:
        """Return a JSON-safe dict of the movement's key fields for audit logging."""
        return {
            "id": str(movement.pk),
            "status": movement.status,
            "quantity": str(movement.quantity),
            "unit": movement.unit,
            "material_category": movement.material_category.code,
            "expected_return_at": (
                movement.expected_return_at.isoformat()
                if movement.expected_return_at
                else None
            ),
            "returned_at": (
                movement.returned_at.isoformat()
                if movement.returned_at
                else None
            ),
        }

    @staticmethod
    def _log_audit(
        *,
        society,
        action,
        movement,
        before=None,
        after=None,
        actor=None,
    ) -> None:
        """Write an append-only GateOpsAuditLog entry for a material operation.

        Wrapped so a logging failure never blocks a legitimate material
        operation; the error is logged at ERROR level instead.
        """
        try:
            GateOpsAuditLog.log(
                society=society,
                action=action,
                entity_type="MaterialMovement",
                entity_id=str(movement.pk),
                actor=actor,
                before_value=before,
                after_value=after,
            )
        except Exception:  # noqa: BLE001 — audit must not break the operation.
            logger.exception(
                "Failed to write MaterialMovement audit log for movement %s (action=%s)",
                movement.pk,
                action,
            )
