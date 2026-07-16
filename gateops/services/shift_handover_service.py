"""Service layer for shift handover management (Phase 12 — Exit Management).

This service is the single authority over :class:`ShiftHandover` and
:class:`ShiftHandoverItem` lifecycle operations. No caller should mutate these
models directly — every state-changing operation must flow through
:class:`ShiftHandoverService` so that:

1. Multi-tenant safety is enforced (every query scoped by ``society``).
2. The *before* state is captured as JSON.
3. The transition is applied (race-safe via ``update()`` where applicable).
4. The *after* state is captured as JSON.
5. A :class:`GateOpsAuditLog` entry is written (append-only).
6. Notifications to the incoming/outgoing guard are dispatched (non-blocking).

Design notes
------------
- **Snapshot philosophy:** ``create_shift_handover`` captures the currently-
  inside persons as immutable :class:`ShiftHandoverItem` rows with denormalized
  fields. The snapshot survives later auto-close/exit transitions on the
  underlying ``GateEvent`` rows.
- **Race safety:** acknowledge/dispute use ``QuerySet.update()`` with a
  ``status=PENDING`` filter so concurrent transitions cannot lose updates.
- **Audit robustness:** audit-log writes are wrapped so a logging failure
  never blocks a legitimate handover operation.
- **All methods are ``@staticmethod``** per the service contract; there is no
  shared mutable state.
"""

from __future__ import annotations

import logging

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, QuerySet
from django.shortcuts import get_object_or_404
from django.utils import timezone

from gateops.models import (
    GateEvent,
    GateEventApproval,
    GateOpsAuditLog,
    GuardShiftAssignment,
    MaterialMovement,
    Parcel,
    ShiftHandover,
    ShiftHandoverItem,
)

logger = logging.getLogger(__name__)


class ShiftHandoverService:
    """Service for ShiftHandover create, acknowledge, dispute, list, and detail.

    Every state-changing operation:
    1. Validates multi-tenant safety (society scoping).
    2. Captures before state as JSON.
    3. Applies the transition (race-safe via ``update()``).
    4. Captures after state as JSON.
    5. Creates a GateOpsAuditLog entry.
    """

    # ------------------------------------------------------------------ #
    # Create
    # ------------------------------------------------------------------ #

    @staticmethod
    @transaction.atomic
    def create_shift_handover(
        *,
        society,
        outgoing_guard,
        incoming_guard,
        gate,
        shift=None,
        outgoing_assignment=None,
        incoming_assignment=None,
        outgoing_notes="",
        actor=None,
    ) -> ShiftHandover:
        """Create a handover record with a snapshot of currently-inside persons.

        - Validates ``outgoing_guard``, ``incoming_guard``, ``gate`` belong to
          ``society`` (the model's ``clean()`` also enforces this, but the
          service fails fast).
        - Validates ``outgoing_guard != incoming_guard``.
        - Checks there is no existing ``PENDING`` handover for the same
          ``(outgoing_guard, gate)`` — prevents duplicate pending handovers.
        - Snapshots currently-inside events (filtered to the gate) into
          :class:`ShiftHandoverItem` rows with denormalized fields.
        - Computes pending items (pending approvals, overdue materials,
          uncollected parcels).
        - Audits a ``HANDOVER_CREATED`` entry.
        - Notifies the incoming guard (non-blocking).
        """
        # Multi-tenant safety: fail fast before hitting the model's clean().
        if outgoing_guard.society_id != society.pk:
            raise ValidationError(
                {"outgoing_guard": "Outgoing guard must belong to the same society."}
            )
        if incoming_guard.society_id != society.pk:
            raise ValidationError(
                {"incoming_guard": "Incoming guard must belong to the same society."}
            )
        if gate.society_id != society.pk:
            raise ValidationError(
                {"gate": "Gate must belong to the same society."}
            )
        if outgoing_guard_id(outgoing_guard) == outgoing_guard_id(incoming_guard):
            raise ValidationError(
                {"incoming_guard": "Incoming guard must differ from outgoing guard."}
            )
        if shift is not None and shift.society_id != society.pk:
            raise ValidationError(
                {"shift": "Shift must belong to the same society."}
            )

        # Check no existing pending handover for this guard+gate.
        existing = ShiftHandover.objects.filter(
            society=society,
            outgoing_guard=outgoing_guard,
            gate=gate,
            status=ShiftHandover.Status.PENDING,
            is_active=True,
        ).exists()
        if existing:
            raise ValidationError(
                "A pending handover already exists for this guard at this gate. "
                "Acknowledge or dispute it first."
            )

        # Snapshot currently-inside events for this gate. We fetch the model
        # instances (with select_related) directly — not the serialized dict —
        # so we can populate the denormalized ShiftHandoverItem fields.
        inside_events = (
            GateEvent.objects.filter(
                society=society,
                gate=gate,
                status=GateEvent.Status.ENTERED,
            )
            .select_related("person", "visitor_category", "gate")
            .order_by("-entered_at")
        )
        now = timezone.now()

        # Compute pending items for the summary.
        pending = ShiftHandoverService._compute_pending_items(society)

        handover = ShiftHandover(
            society=society,
            outgoing_guard=outgoing_guard,
            incoming_guard=incoming_guard,
            gate=gate,
            shift=shift,
            outgoing_assignment=outgoing_assignment,
            incoming_assignment=incoming_assignment,
            status=ShiftHandover.Status.PENDING,
            inside_count=inside_events.count(),
            pending_items_count=pending["total"],
            pending_items_summary=pending["summary"],
            outgoing_notes=outgoing_notes,
            created_by=actor,
        )
        handover.save()

        # Create immutable snapshot items for each inside event. We iterate
        # the already-fetched queryset; the count() above ran a COUNT query but
        # the items loop re-evaluates — acceptable for v1 (a busy gate rarely
        # has hundreds inside simultaneously). If profiling shows this is slow,
        # switch to values_list + bulk_create.
        items_to_create = []
        for event in inside_events:
            duration_minutes = 0
            if event.entered_at is not None:
                duration_minutes = int(
                    (now - event.entered_at).total_seconds() // 60
                )
            is_overstay = (
                event.auto_close_at is not None and event.auto_close_at <= now
            )
            items_to_create.append(
                ShiftHandoverItem(
                    society=society,
                    handover=handover,
                    gate_event=event,
                    person=event.person,
                    visitor_category=event.visitor_category,
                    entered_at=event.entered_at,
                    duration_minutes_at_handover=duration_minutes,
                    gate=event.gate,
                    is_overstay=is_overstay,
                )
            )
        if items_to_create:
            # bulk_create bypasses save()/clean(); the service has already
            # validated cross-society consistency above, and the items are
            # denormalized snapshots of already-validated GateEvent rows.
            ShiftHandoverItem.objects.bulk_create(items_to_create)

        after = ShiftHandoverService._serialize_handover(handover)
        ShiftHandoverService._log_audit(
            society=society,
            action=GateOpsAuditLog.Action.HANDOVER_CREATED,
            entity_type="ShiftHandover",
            entity_id=handover.pk,
            before=None,
            after=after,
            actor=actor,
        )

        # Non-blocking notification to the incoming guard.
        ShiftHandoverService._notify_incoming_guard(handover)

        return handover

    # ------------------------------------------------------------------ #
    # Acknowledge
    # ------------------------------------------------------------------ #

    @staticmethod
    @transaction.atomic
    def acknowledge_handover(
        *, society, handover_id, incoming_guard, notes="", actor=None
    ) -> ShiftHandover:
        """Acknowledge receipt of a handover.

        - Fetches the handover (society-scoped, ``is_active=True``).
        - Validates ``status == PENDING`` (cannot acknowledge an already-
          acknowledged or disputed handover). Note: a DISPUTED handover CAN be
          acknowledged (dispute resolved → acknowledged).
        - Validates ``incoming_guard == handover.incoming_guard``.
        - Race-safe transition via ``update()``.
        - Audits a ``HANDOVER_ACKNOWLEDGED`` entry.
        - Notifies the outgoing guard (non-blocking).
        """
        handover = ShiftHandoverService._get_handover(
            society=society, handover_id=handover_id
        )

        # Only PENDING or DISPUTED can transition to ACKNOWLEDGED.
        if handover.status == ShiftHandover.Status.ACKNOWLEDGED:
            raise ValidationError("Handover is already acknowledged.")
        if handover.status not in (
            ShiftHandover.Status.PENDING,
            ShiftHandover.Status.DISPUTED,
        ):
            raise ValidationError(
                f"Cannot acknowledge a handover with status '{handover.status}'."
            )

        # Only the designated incoming guard may acknowledge.
        if handover.incoming_guard_id != incoming_guard.pk:
            raise ValidationError(
                "Only the designated incoming guard may acknowledge this handover."
            )

        before = ShiftHandoverService._serialize_handover(handover)
        now = timezone.now()
        # Race-safe: only update if still in a pre-acknowledged state.
        updated = ShiftHandover.objects.filter(
            pk=handover.pk,
            status__in=[
                ShiftHandover.Status.PENDING,
                ShiftHandover.Status.DISPUTED,
            ],
        ).update(
            status=ShiftHandover.Status.ACKNOWLEDGED,
            acknowledged_at=now,
            acknowledged_by=actor,
            incoming_notes=notes,
        )
        if updated == 0:
            # Another process acknowledged it first.
            raise ValidationError("Handover is no longer pending or disputed.")
        handover.refresh_from_db()
        after = ShiftHandoverService._serialize_handover(handover)
        ShiftHandoverService._log_audit(
            society=society,
            action=GateOpsAuditLog.Action.HANDOVER_ACKNOWLEDGED,
            entity_type="ShiftHandover",
            entity_id=handover.pk,
            before=before,
            after=after,
            actor=actor,
        )
        ShiftHandoverService._notify_outgoing_guard(handover, "acknowledged")
        return handover

    # ------------------------------------------------------------------ #
    # Dispute
    # ------------------------------------------------------------------ #

    @staticmethod
    @transaction.atomic
    def dispute_handover(
        *, society, handover_id, incoming_guard, reason, actor=None
    ) -> ShiftHandover:
        """Mark a handover as disputed.

        - Fetches the handover (society-scoped).
        - Validates ``status == PENDING`` (cannot dispute an already-
          acknowledged or disputed handover).
        - Validates ``incoming_guard == handover.incoming_guard``.
        - ``reason`` is required (non-empty).
        - Race-safe transition via ``update()``.
        - Audits a ``HANDOVER_DISPUTED`` entry.
        - Notifies the outgoing guard AND society admin (non-blocking).
        """
        reason = (reason or "").strip()
        if not reason:
            raise ValidationError({"reason": "A dispute reason is required."})

        handover = ShiftHandoverService._get_handover(
            society=society, handover_id=handover_id
        )

        if handover.status != ShiftHandover.Status.PENDING:
            raise ValidationError(
                f"Cannot dispute a handover with status '{handover.status}'. "
                "Only a pending handover can be disputed."
            )

        if handover.incoming_guard_id != incoming_guard.pk:
            raise ValidationError(
                "Only the designated incoming guard may dispute this handover."
            )

        before = ShiftHandoverService._serialize_handover(handover)
        now = timezone.now()
        updated = ShiftHandover.objects.filter(
            pk=handover.pk,
            status=ShiftHandover.Status.PENDING,
        ).update(
            status=ShiftHandover.Status.DISPUTED,
            disputed_at=now,
            dispute_reason=reason,
        )
        if updated == 0:
            raise ValidationError("Handover is no longer pending.")
        handover.refresh_from_db()
        after = ShiftHandoverService._serialize_handover(handover)
        ShiftHandoverService._log_audit(
            society=society,
            action=GateOpsAuditLog.Action.HANDOVER_DISPUTED,
            entity_type="ShiftHandover",
            entity_id=handover.pk,
            before=before,
            after=after,
            actor=actor,
        )
        ShiftHandoverService._notify_outgoing_guard(handover, "disputed")
        return handover

    # ------------------------------------------------------------------ #
    # List / Detail
    # ------------------------------------------------------------------ #

    @staticmethod
    def list_handovers(
        *, society, status=None, gate=None, guard=None, include_inactive=False
    ) -> QuerySet:
        """List handovers for a society with optional filters.

        Ordered by ``-handed_over_at``. Uses ``select_related`` on the guard,
        gate, and shift FKs to avoid N+1 on display.
        """
        qs = ShiftHandover.objects.filter(society=society).select_related(
            "outgoing_guard",
            "incoming_guard",
            "gate",
            "shift",
            "outgoing_assignment",
            "incoming_assignment",
        )
        if not include_inactive:
            qs = qs.filter(is_active=True)
        if status:
            qs = qs.filter(status=status)
        if gate:
            qs = qs.filter(gate=gate)
        if guard:
            qs = qs.filter(Q(outgoing_guard=guard) | Q(incoming_guard=guard))
        return qs.order_by("-handed_over_at")

    @staticmethod
    def get_handover(*, society, handover_id) -> ShiftHandover:
        """Fetch a single society-scoped handover or raise Http404.

        Accepts a UUID (``handover_uuid``) or integer PK.
        """
        return ShiftHandoverService._get_handover(
            society=society, handover_id=handover_id
        )

    @staticmethod
    def get_handover_items(*, society, handover_id) -> QuerySet:
        """List the ShiftHandoverItem rows for a handover. Society-scoped."""
        handover = ShiftHandoverService._get_handover(
            society=society, handover_id=handover_id
        )
        return handover.items.select_related(
            "person", "visitor_category", "gate", "gate_event"
        ).order_by("-entered_at")

    @staticmethod
    def get_pending_handovers_for_guard(*, society, guard) -> QuerySet:
        """Pending handovers where ``incoming_guard=guard``.

        For the "You have a handover to acknowledge" alert.
        """
        return ShiftHandover.objects.filter(
            society=society,
            incoming_guard=guard,
            status=ShiftHandover.Status.PENDING,
            is_active=True,
        ).select_related("outgoing_guard", "gate", "shift").order_by(
            "-handed_over_at"
        )

    @staticmethod
    def get_guards_needing_handover(*, society, at=None) -> QuerySet:
        """Return GuardShiftAssignments where the shift has ended but no
        handover has been created and check_out_at is null.

        Used by the handover-create view to pre-fill the outgoing guard and
        gate, and by the optional reminder command. This is **not** a hard
        gate — a guard can create a handover even if the system doesn't think
        one is needed (ad-hoc handover).
        """
        at = at or timezone.now()
        today = at.date()
        return (
            GuardShiftAssignment.objects.filter(
                society=society,
                date=today,
                check_out_at__isnull=True,
            )
            .exclude(
                outgoing_handovers__status__in=[
                    ShiftHandover.Status.PENDING,
                    ShiftHandover.Status.ACKNOWLEDGED,
                ],
                outgoing_handovers__is_active=True,
            )
            .select_related("guard", "shift", "gate")
        )

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _get_handover(*, society, handover_id) -> ShiftHandover:
        """Fetch a handover by UUID or PK, society-scoped + is_active.

        Raises Http404 if not found or cross-society.
        """
        import uuid as _uuid

        qs = ShiftHandover.objects.filter(society=society, is_active=True)
        # Try UUID first, then int PK.
        try:
            uuid_obj = _uuid.UUID(str(handover_id))
            return get_object_or_404(qs, handover_uuid=uuid_obj)
        except (ValueError, TypeError):
            pass
        try:
            pk = int(handover_id)
            return get_object_or_404(qs, pk=pk)
        except (ValueError, TypeError):
            from django.http import Http404

            raise Http404("ShiftHandover not found.")

    @staticmethod
    def _compute_pending_items(society) -> dict:
        """Compute pending items for a handover snapshot.

        Returns ``{"total": int, "summary": {pending_approvals, overdue_materials,
        uncollected_parcels}}``.
        """
        pending_approvals = GateEventApproval.objects.filter(
            society=society,
            decision=GateEventApproval.Decision.PENDING,
        ).count()

        now = timezone.now()
        overdue_materials = MaterialMovement.objects.filter(
            society=society,
            status=MaterialMovement.Status.IN_TRANSIT,
            expected_return_at__lt=now,
        ).count()

        uncollected_parcels = Parcel.objects.filter(
            society=society,
            status=Parcel.Status.RECEIVED,
        ).count()

        total = pending_approvals + overdue_materials + uncollected_parcels
        return {
            "total": total,
            "summary": {
                "pending_approvals": pending_approvals,
                "overdue_materials": overdue_materials,
                "uncollected_parcels": uncollected_parcels,
            },
        }

    @staticmethod
    def _serialize_handover(handover) -> dict:
        """Return a JSON-safe dict of the handover's key fields for audit."""
        def _dt(value):
            return value.isoformat() if value else None

        return {
            "id": str(handover.pk),
            "handover_uuid": str(handover.handover_uuid),
            "status": handover.status,
            "outgoing_guard_id": str(handover.outgoing_guard_id),
            "incoming_guard_id": str(handover.incoming_guard_id),
            "gate_id": str(handover.gate_id),
            "shift_id": str(handover.shift_id) if handover.shift_id else None,
            "inside_count": handover.inside_count,
            "pending_items_count": handover.pending_items_count,
            "pending_items_summary": handover.pending_items_summary,
            "handed_over_at": _dt(handover.handed_over_at),
            "acknowledged_at": _dt(handover.acknowledged_at),
            "disputed_at": _dt(handover.disputed_at),
            "is_active": handover.is_active,
        }

    @staticmethod
    def _log_audit(
        *,
        society,
        action,
        entity_type,
        entity_id,
        before=None,
        after=None,
        actor=None,
    ) -> None:
        """Write an append-only GateOpsAuditLog entry for a handover operation.

        Wrapped so a logging failure never blocks a legitimate handover
        operation; the error is logged at ERROR level instead.
        """
        try:
            GateOpsAuditLog.log(
                society=society,
                action=action,
                entity_type=entity_type,
                entity_id=str(entity_id) if entity_id is not None else "",
                actor=actor,
                before_value=before,
                after_value=after,
            )
        except Exception:  # noqa: BLE001 — audit must not break the operation.
            logger.exception(
                "Failed to write %s audit log for entity %s (action=%s)",
                entity_type,
                entity_id,
                action,
            )

    @staticmethod
    def _notify_incoming_guard(handover) -> None:
        """Non-blocking notification to the incoming guard of a pending handover.

        Uses the notification engine if a suitable trigger exists, or falls back
        to direct EmailQueue creation for the incoming guard's linked User.
        Wrapped in try/except so notification failures NEVER block handover
        creation.
        """
        try:
            # Resolve the incoming guard's linked User (if any).
            user = getattr(handover.incoming_guard, "user", None)
            if user is None:
                return
            # Defer the import to avoid a circular dependency at module load.
            from notifications.models import EmailQueue

            EmailQueue.objects.create(
                society=handover.society,
                to_email=user.email or "",
                subject=f"Shift handover pending — {handover.society.name}",
                body=(
                    f"You have a pending shift handover from "
                    f"{handover.outgoing_guard} at gate {handover.gate}.\n"
                    f"Inside count: {handover.inside_count}\n"
                    f"Pending items: {handover.pending_items_count}\n"
                    f"Notes: {handover.outgoing_notes}\n"
                    f"Please acknowledge or dispute at your earliest convenience."
                ),
                status="pending",
            )
        except Exception:  # noqa: BLE001 — notifications must not break handovers.
            logger.exception(
                "Failed to notify incoming guard for handover %s",
                handover.pk,
            )

    @staticmethod
    def _notify_outgoing_guard(handover, event_type) -> None:
        """Non-blocking notification to the outgoing guard of an ack/dispute.

        ``event_type`` is "acknowledged" or "disputed".
        """
        try:
            user = getattr(handover.outgoing_guard, "user", None)
            if user is None:
                return
            from notifications.models import EmailQueue

            if event_type == "acknowledged":
                subject = f"Shift handover acknowledged — {handover.society.name}"
                body = (
                    f"Your handover at gate {handover.gate} has been acknowledged "
                    f"by {handover.incoming_guard}."
                )
            else:
                subject = f"Shift handover DISPUTED — {handover.society.name}"
                body = (
                    f"Your handover at gate {handover.gate} has been DISPUTED "
                    f"by {handover.incoming_guard}.\n"
                    f"Reason: {handover.dispute_reason}"
                )
            EmailQueue.objects.create(
                society=handover.society,
                to_email=user.email or "",
                subject=subject,
                body=body,
                status="pending",
            )
        except Exception:  # noqa: BLE001 — notifications must not break handovers.
            logger.exception(
                "Failed to notify outgoing guard for handover %s (event=%s)",
                handover.pk,
                event_type,
            )


def outgoing_guard_id(guard):
    """Return the PK of a guard instance or None (helper for comparisons)."""
    return getattr(guard, "pk", None)
