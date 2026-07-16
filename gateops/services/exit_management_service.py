"""Service layer for exit management (Phase 12 — Exit Management).

This service is a thin orchestration layer over
:class:`GateEventLifecycleService`. It provides:

1. **One-tap exit** — :meth:`ExitManagementService.process_quick_exit` resolves
   a ``GateEvent`` by UUID/PK, validates it is currently inside, and delegates
   the transition to :meth:`GateEventLifecycleService.record_exit`.
2. **QR exit** — :meth:`ExitManagementService.process_qr_exit` resolves a
   scanned QR string (a ``Pass.code`` or ``GateEvent.event_uuid``) to the
   matching inside event, then delegates to ``record_exit``.
3. **Currently Inside query** — :meth:`ExitManagementService.get_currently_inside`
   returns a paginated, filtered, duration-annotated list of all ENTERED
   events for a society.

Design principles (from the Phase 12 design doc):

- **Delegate, don't duplicate.** The exit *transition* is owned by
  ``GateEventLifecycleService.record_exit()``. This service never sets
  ``status=EXITED`` directly — it resolves, validates, and delegates.
- **Multi-tenant safety.** Every query is scoped by ``society``. Cross-tenant
  access raises ``DoesNotExist`` / ``ValidationError``.
- **Non-blocking audit.** ``record_exit()`` already writes the ``EXIT`` audit
  log entry; this service does not add a second one.
- **Service contract.** All methods are ``@staticmethod``, use keyword-only
  args, and wrap writes in ``@transaction.atomic``.
"""

from __future__ import annotations

import logging
import uuid

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from gateops.models import GateEvent, Pass, ShiftHandover

logger = logging.getLogger(__name__)

# Cache TTL (seconds) for the "currently inside" count badge. The list itself
# is always live; only the count is cached to avoid a COUNT(*) on every
# dashboard refresh.
_INSIDE_COUNT_CACHE_TTL = 60


class ExitManagementService:
    """One-tap exit, QR exit, and the "currently inside" query.

    Every exit method:
    1. Resolves the event (by UUID/PK or QR code).
    2. Validates society scope (cross-tenant rejection).
    3. Validates ``status == ENTERED`` (friendlier error before the state machine).
    4. Delegates to :meth:`GateEventLifecycleService.record_exit`.
    """

    # ------------------------------------------------------------------ #
    # Exit operations
    # ------------------------------------------------------------------ #

    @staticmethod
    @transaction.atomic
    def process_quick_exit(*, society, gate_event_id, gate=None, guard=None, actor=None) -> GateEvent:
        """One-tap exit by GateEvent UUID or PK.

        Resolves the event, validates it is currently inside, and delegates the
        transition to :meth:`GateEventLifecycleService.record_exit`.

        ``gate_event_id`` accepts either a UUID string (the ``event_uuid``) or
        an integer PK. Cross-gate exit is allowed by default (a visitor may
        exit at any gate); the entry gate is preserved on the event.

        ``actor`` is accepted for service-contract consistency (every
        state-changing service method accepts it). The EXIT audit log is
        written by :meth:`GateEventLifecycleService.record_exit`; this service
        does not add a second entry.
        """
        event = ExitManagementService._resolve_event(society=society, gate_event_id=gate_event_id)
        ExitManagementService._validate_inside(event)
        # Cross-gate exit is allowed by default. A future society-config toggle
        # (restrict_exit_to_entry_gate) could enforce same-gate exit; for now we
        # simply note the difference without blocking.
        if gate is not None and event.gate_id != gate.pk:
            logger.info(
                "Cross-gate exit: event %s entered at gate %s, exiting at gate %s.",
                event.pk,
                event.gate_id,
                gate.pk,
            )
        # Local import to avoid a circular dependency at module load time.
        from gateops.services.gate_event_lifecycle import GateEventLifecycleService

        return GateEventLifecycleService.record_exit(event, guard=guard)

    @staticmethod
    @transaction.atomic
    def process_qr_exit(*, society, qr_code, gate=None, guard=None, actor=None) -> GateEvent:
        """QR-code-based exit: resolve Pass code or GateEvent UUID, then exit.

        Resolution order:
        1. If ``qr_code`` is a valid UUID and matches a ``GateEvent`` in the
           society, use that event directly.
        2. Otherwise, look up ``Pass`` by ``code`` (society-scoped, active). If
           found, find the ``GateEvent`` with ``pass_ref=pass``,
           ``status=ENTERED`` (most recent by ``entered_at``).
        3. If neither resolves, raise ``ValidationError``.

        The pass is NOT mutated on exit — ``usage_count`` tracks entries, not
        exits.

        ``actor`` is accepted for service-contract consistency (every
        state-changing service method accepts it). The EXIT audit log is
        written by :meth:`GateEventLifecycleService.record_exit`; this service
        does not add a second entry.
        """
        event = ExitManagementService._resolve_qr(society=society, qr_code=qr_code)
        ExitManagementService._validate_inside(event)
        from gateops.services.gate_event_lifecycle import GateEventLifecycleService

        return GateEventLifecycleService.record_exit(event, guard=guard)

    # ------------------------------------------------------------------ #
    # Currently Inside query
    # ------------------------------------------------------------------ #

    @staticmethod
    def get_currently_inside(
        *, society, filters=None, page=None, page_size=50
    ) -> dict:
        """Return a paginated, filtered list of persons currently inside.

        Returns a dict with ``results`` (list of serialized event dicts),
        ``total`` (count), ``page``, ``page_size``, ``total_pages``.

        Filters (all optional, passed as a dict):
        - ``gate_id`` — filter by entry gate.
        - ``visitor_category_id`` — filter by visitor category.
        - ``person_id`` — filter by specific person.
        - ``host_unit_id`` — filter by host unit.
        - ``min_duration_minutes`` — only persons inside longer than this.
        - ``max_duration_minutes`` — only persons inside shorter than this.
        - ``is_overstay`` — boolean; if True, filter ``auto_close_at__lte=now``.
        - ``search`` — text search on person name/phone (icontains).
        """
        qs = GateEvent.objects.filter(
            society=society, status=GateEvent.Status.ENTERED
        ).select_related("person", "visitor_category", "gate", "guard", "host_unit")

        filters = filters or {}
        if filters.get("gate_id"):
            qs = qs.filter(gate_id=filters["gate_id"])
        if filters.get("visitor_category_id"):
            qs = qs.filter(visitor_category_id=filters["visitor_category_id"])
        if filters.get("person_id"):
            qs = qs.filter(person_id=filters["person_id"])
        if filters.get("host_unit_id"):
            qs = qs.filter(host_unit_id=filters["host_unit_id"])
        if filters.get("min_duration_minutes") is not None:
            cutoff = timezone.now() - _minutes_to_timedelta(filters["min_duration_minutes"])
            qs = qs.filter(entered_at__lte=cutoff)
        if filters.get("max_duration_minutes") is not None:
            cutoff = timezone.now() - _minutes_to_timedelta(filters["max_duration_minutes"])
            qs = qs.filter(entered_at__gte=cutoff)
        if filters.get("is_overstay"):
            qs = qs.filter(auto_close_at__lte=timezone.now())
        if filters.get("search"):
            qs = qs.filter(
                Q(person__name__icontains=filters["search"])
                | Q(person__phone__icontains=filters["search"])
            )

        qs = qs.order_by("-entered_at")
        total = qs.count()
        paginator = Paginator(qs, page_size)
        page_obj = paginator.get_page(page)
        now = timezone.now()
        results = [
            ExitManagementService._serialize_inside_event(e, now)
            for e in page_obj.object_list
        ]
        return {
            "results": results,
            "total": total,
            "page": page_obj.number,
            "page_size": page_size,
            "total_pages": paginator.num_pages,
        }

    @staticmethod
    def get_currently_inside_count(*, society, gate=None) -> int:
        """Lightweight count of ENTERED events for dashboard badges.

        Cached for 60 seconds via Django's cache framework to avoid hammering
        the DB on dashboard refresh. The cache key is scoped by society (and
        optionally gate). The paginated list is always live; only the count is
        cached.
        """
        cache_key = ExitManagementService._get_cache_key(society=society, gate=gate)
        qs = GateEvent.objects.filter(society=society, status=GateEvent.Status.ENTERED)
        if gate is not None:
            qs = qs.filter(gate=gate)
        # cache.get_or_set avoids a second round-trip: the callable runs only
        # on a cache miss.
        return cache.get_or_set(cache_key, lambda: qs.count(), _INSIDE_COUNT_CACHE_TTL)

    @staticmethod
    def get_pending_handover_count(*, society, guard=None) -> int:
        """Count of pending handovers for a society (optionally per incoming guard).

        Used to surface "You have a handover to acknowledge" alerts.
        """
        qs = ShiftHandover.objects.filter(
            society=society,
            status=ShiftHandover.Status.PENDING,
            is_active=True,
        )
        if guard is not None:
            qs = qs.filter(incoming_guard=guard)
        return qs.count()

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _resolve_event(*, society, gate_event_id) -> GateEvent:
        """Normalize ``gate_event_id`` (UUID string or int PK) and fetch the
        society-scoped event.

        Raises ``GateEvent.DoesNotExist`` (which the view converts to Http404)
        if not found or cross-society.
        """
        # Try UUID first, then int PK.
        try:
            uuid_obj = uuid.UUID(str(gate_event_id))
            return GateEvent.objects.get(society=society, event_uuid=uuid_obj)
        except (ValueError, TypeError):
            pass
        try:
            pk = int(gate_event_id)
            return GateEvent.objects.get(society=society, pk=pk)
        except (ValueError, TypeError):
            raise GateEvent.DoesNotExist("GateEvent not found.")

    @staticmethod
    def _resolve_qr(*, society, qr_code) -> GateEvent:
        """Resolve a QR code string to a ``GateEvent``.

        Tries UUID lookup first, then ``Pass.code`` lookup. Raises
        ``ValidationError`` if neither resolves.
        """
        # 1. Try as GateEvent UUID.
        try:
            uuid_obj = uuid.UUID(str(qr_code))
            event = GateEvent.objects.filter(
                society=society, event_uuid=uuid_obj
            ).first()
            if event is not None:
                return event
        except (ValueError, TypeError):
            pass
        # 2. Try as Pass code.
        pass_obj = Pass.objects.filter(
            society=society, code=qr_code, is_active=True
        ).first()
        if pass_obj is not None:
            event = (
                GateEvent.objects.filter(
                    society=society,
                    pass_ref=pass_obj,
                    status=GateEvent.Status.ENTERED,
                )
                .order_by("-entered_at")
                .first()
            )
            if event is not None:
                return event
        raise ValidationError(
            "Invalid QR code: no matching pass or gate event found."
        )

    @staticmethod
    def _validate_inside(event) -> None:
        """Raise ``ValidationError`` if ``event.status != ENTERED``."""
        if event.status != GateEvent.Status.ENTERED:
            raise ValidationError(
                f"Event is not currently inside; current status: {event.status}."
            )

    @staticmethod
    def _serialize_inside_event(event, now) -> dict:
        """Serialize a "currently inside" event for the view/API layer."""
        return {
            "id": event.pk,
            "event_uuid": str(event.event_uuid),
            "person_name": event.person.name if event.person else "Unknown",
            "person_phone": event.person.phone if event.person else "",
            "visitor_category": event.visitor_category.name if event.visitor_category else None,
            "visitor_category_code": event.visitor_category.code if event.visitor_category else None,
            "gate": event.gate.name if event.gate else None,
            "gate_code": event.gate.code if event.gate else None,
            "entered_at": event.entered_at.isoformat() if event.entered_at else None,
            "duration_minutes": (
                int((now - event.entered_at).total_seconds() // 60)
                if event.entered_at
                else 0
            ),
            "is_overstay": (
                event.auto_close_at is not None and event.auto_close_at <= now
            ),
            "auto_close_at": (
                event.auto_close_at.isoformat() if event.auto_close_at else None
            ),
            "host_unit": str(event.host_unit) if event.host_unit else None,
            "pass_code": event.pass_ref.code if event.pass_ref else None,
        }

    @staticmethod
    def _get_cache_key(*, society, gate=None) -> str:
        """Build the cache key for the inside-count badge."""
        if gate is not None:
            return f"gateops:inside_count:{society.pk}:{gate.pk}"
        return f"gateops:inside_count:{society.pk}"


def _minutes_to_timedelta(minutes):
    """Convert a numeric/str minutes value to a ``timedelta``.

    Accepts int, float, or numeric string (from GET query params). Raises
    ``ValidationError`` for non-numeric input so the view returns a friendly
    error instead of a 500.
    """
    from datetime import timedelta

    try:
        return timedelta(minutes=float(minutes))
    except (TypeError, ValueError):
        raise ValidationError("Duration minutes must be a number.")
