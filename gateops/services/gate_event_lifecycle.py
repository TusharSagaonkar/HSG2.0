"""State machine service for the ``GateEvent`` lifecycle (Phase 3).

This service is the single authority over :class:`GateEvent` status transitions.
No caller should mutate ``event.status`` directly — every transition must flow
through :class:`GateEventLifecycleService` so that:

1. The transition is validated against the legal state machine.
2. The *before* state is captured as JSON.
3. The transition is applied (with method-specific fields).
4. The *after* state is captured as JSON.
5. A :class:`GateOpsAuditLog` entry is written (append-only).

The service also bridges the rule engine (:class:`RuleEngineService`) to the
lifecycle: after an arrival, :meth:`evaluate_rules` runs the configured rules
and applies the resulting action (auto-approve, reject, require approval,
direct entry, emergency override).

Design notes
------------
- **Blacklist invariant (#4):** a blacklisted person can never be
  auto-approved. Even when the rule engine returns ``AUTO_APPROVE``, a
  blacklisted person is held in ``arrived`` and a manual approval request is
  created instead.
- **Graceful degradation:** rule-engine failures are caught and degraded to a
  pending approval request (the safe middle ground), matching
  ``RuleEngineService``'s own degradation philosophy.
- **Audit robustness:** audit-log writes are wrapped so a logging failure never
  blocks a legitimate gate operation (the error is logged loudly instead).
- **All methods are ``@staticmethod``** per the service contract; there is no
  shared mutable state.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.utils import timezone

from gateops.models import (
    GateEvent,
    GateEventApproval,
    GateOpsAuditLog,
    GateOpsSocietyConfig,
    Rule,
    RuleAction,
)
from gateops.services.rule_engine import RuleEngineService

logger = logging.getLogger(__name__)

# Hours used when a society has no GateOpsSocietyConfig row. The model default
# is 12, but the lifecycle spec mandates 24 as the safe fallback for missing
# config so forgotten exits are still closed within a day.
DEFAULT_AUTO_CLOSE_HOURS = 24


class GateEventLifecycleService:
    """State machine service for GateEvent lifecycle transitions.

    Every transition:
    1. Validates the transition is legal (state machine)
    2. Captures before state as JSON
    3. Applies the transition
    4. Captures after state as JSON
    5. Creates a GateOpsAuditLog entry
    """

    # ------------------------------------------------------------------ #
    # State machine definition
    # ------------------------------------------------------------------ #
    #
    # Maps a *current* status to the set of statuses it may legally move to.
    # Any transition not listed here raises ``ValidationError``.
    _TRANSITIONS: dict[str, set[str]] = {
        GateEvent.Status.INVITED: {
            GateEvent.Status.ARRIVED,
            GateEvent.Status.CANCELLED,
            GateEvent.Status.EXPIRED,
        },
        GateEvent.Status.ARRIVED: {
            GateEvent.Status.APPROVED,
            GateEvent.Status.REJECTED,
            GateEvent.Status.EXPIRED,
        },
        GateEvent.Status.APPROVED: {
            GateEvent.Status.ENTERED,
        },
        GateEvent.Status.ENTERED: {
            GateEvent.Status.EXITED,
            GateEvent.Status.AUTO_CLOSED,
        },
        # Terminal states: no outgoing transitions.
        GateEvent.Status.REJECTED: set(),
        GateEvent.Status.EXITED: set(),
        GateEvent.Status.AUTO_CLOSED: set(),
        GateEvent.Status.CANCELLED: set(),
        GateEvent.Status.EXPIRED: set(),
    }

    # ------------------------------------------------------------------ #
    # Public lifecycle API
    # ------------------------------------------------------------------ #

    @staticmethod
    def create_invitation(
        society,
        visitor_category,
        person,
        expected_arrival_at,
        created_by,
        gate=None,
        purpose="",
        direction="inbound",
    ):
        """Create a GateEvent with status=invited, event_type=invitation.

        ``gate`` is required by the ``GateEvent`` model (non-nullable FK); it is
        accepted as a keyword for ergonomic call sites but a ``None`` gate
        raises ``ValidationError`` rather than failing later at the DB layer.
        """
        if gate is None:
            raise ValidationError({"gate": "gate is required to create an invitation."})

        event = GateEvent(
            society=society,
            gate=gate,
            person=person,
            visitor_category=visitor_category,
            event_type=GateEvent.EventType.INVITATION,
            status=GateEvent.Status.INVITED,
            direction=direction,
            purpose=purpose or "",
            expected_arrival_at=expected_arrival_at,
            created_by=created_by,
        )
        event.save()

        after = GateEventLifecycleService._serialize(event)
        GateEventLifecycleService._log_audit(
            event,
            GateOpsAuditLog.Action.CREATE,
            None,
            after,
            actor=created_by,
        )
        return event

    @staticmethod
    def record_arrival(event, gate, guard=None, photo_url=""):
        """Transition: invited → arrived.

        Sets ``arrived_at``, ``gate``, ``guard``, ``photo_url`` and then runs
        :meth:`evaluate_rules` to drive the next state based on configured
        rules. Walk-in arrivals (``event is None``) are not supported by this
        signature — create an invitation (or an arrived event) first.
        """
        if event is None:
            raise ValidationError(
                "record_arrival requires an existing invited GateEvent; "
                "create one via create_invitation() first."
            )

        before = GateEventLifecycleService._serialize(event)
        GateEventLifecycleService._validate_transition(event, GateEvent.Status.ARRIVED)

        now = timezone.now()
        event.status = GateEvent.Status.ARRIVED
        event.arrived_at = now
        event.gate = gate
        if guard is not None:
            event.guard = guard
        if photo_url:
            event.photo_url = photo_url
        event.save()

        after = GateEventLifecycleService._serialize(event)
        GateEventLifecycleService._log_audit(
            event,
            GateOpsAuditLog.Action.STATE_TRANSITION,
            before,
            after,
            actor=None,
        )

        # Drive the next state from the rule engine.
        GateEventLifecycleService.evaluate_rules(event)
        return event

    @staticmethod
    def evaluate_rules(event):
        """Evaluate configured rules for the event and apply the action.

        Builds a context dict, calls ``RuleEngineService.evaluate``, caches the
        result on the event (``rule_evaluated`` / ``rule_action``), and applies
        the action:

        - ``AUTO_APPROVE`` → approved (unless blacklisted → approval request)
        - ``REJECT`` → rejected
        - ``REQUIRE_APPROVAL`` / ``REQUIRE_RESIDENT_APPROVAL`` → approval request
        - ``DIRECT_ENTRY`` / ``EMERGENCY_OVERRIDE`` → approved then entered
        - anything else → approval request (safe default)

        Returns the :class:`RuleEvaluationResult` (or ``None`` if the engine
        itself raised and we degraded to an approval request).
        """
        context = GateEventLifecycleService._build_rule_context(event)

        try:
            result = RuleEngineService.evaluate(context)
        except Exception as exc:  # noqa: BLE001 — degrade gracefully.
            logger.exception(
                "Rule engine evaluation failed for GateEvent %s: %s", event.pk, exc
            )
            event.rule_action = RuleAction.ActionType.REQUIRE_APPROVAL
            event.save()
            GateEventLifecycleService._create_approval_request(
                event, notes=f"Rule engine error: {exc}"
            )
            return None

        # Cache the decision on the event. The RuleEvaluation row is treated as
        # append-only (per its docstring), so we only set the forward FK here.
        event.rule_evaluated = result.evaluation
        event.rule_action = result.action

        action = result.action
        person = event.person
        is_blacklisted = bool(person is not None and person.is_blacklisted)

        if action == RuleAction.ActionType.AUTO_APPROVE:
            if not is_blacklisted:
                GateEventLifecycleService._apply_transition(
                    event,
                    GateEvent.Status.APPROVED,
                    action=GateOpsAuditLog.Action.APPROVE,
                    extra_fields={"approved_at": timezone.now()},
                )
            else:
                # Invariant #4: blacklisted persons cannot be auto-approved.
                GateEventLifecycleService._create_approval_request(
                    event,
                    notes="Auto-approve blocked: person is blacklisted.",
                )
                event.save()

        elif action == RuleAction.ActionType.REJECT:
            GateEventLifecycleService._apply_transition(
                event,
                GateEvent.Status.REJECTED,
                action=GateOpsAuditLog.Action.REJECT,
                extra_fields={"event_type": GateEvent.EventType.REJECTED},
            )

        elif action in (
            RuleAction.ActionType.REQUIRE_APPROVAL,
            RuleAction.ActionType.REQUIRE_RESIDENT_APPROVAL,
        ):
            GateEventLifecycleService._create_approval_request(event)
            event.save()

        elif action in (
            RuleAction.ActionType.DIRECT_ENTRY,
            RuleAction.ActionType.EMERGENCY_OVERRIDE,
        ):
            # arrived → approved → entered in one flow.
            GateEventLifecycleService._apply_transition(
                event,
                GateEvent.Status.APPROVED,
                action=GateOpsAuditLog.Action.APPROVE,
                extra_fields={"approved_at": timezone.now()},
            )
            GateEventLifecycleService.record_entry(event)

        else:
            # NOTIFY_SECURITY, FLAG_FOR_REVIEW, SEND_NOTIFICATION, ESCALATE:
            # surface to a human approver (safe middle ground).
            GateEventLifecycleService._create_approval_request(event)
            event.save()

        return result

    @staticmethod
    def approve(event, approved_by, method="app", notes=""):
        """Transition: arrived → approved.

        Sets ``approved_at`` / ``approved_by`` and records the decision on the
        latest pending :class:`GateEventApproval` (creating one if none exists).
        """
        before = GateEventLifecycleService._serialize(event)
        GateEventLifecycleService._validate_transition(event, GateEvent.Status.APPROVED)

        now = timezone.now()
        event.status = GateEvent.Status.APPROVED
        event.approved_at = now
        event.approved_by = approved_by
        event.save()

        GateEventLifecycleService._record_approval_decision(
            event=event,
            decision=GateEventApproval.Decision.APPROVED,
            decided_by=approved_by,
            decided_at=now,
            method=method,
            notes=notes,
        )

        after = GateEventLifecycleService._serialize(event)
        GateEventLifecycleService._log_audit(
            event,
            GateOpsAuditLog.Action.APPROVE,
            before,
            after,
            actor=approved_by,
        )
        return event

    @staticmethod
    def reject(event, decided_by, reason=""):
        """Transition: arrived → rejected.

        Sets ``event_type=rejected`` and records the decision on the latest
        pending :class:`GateEventApproval` (creating one if none exists).
        """
        before = GateEventLifecycleService._serialize(event)
        GateEventLifecycleService._validate_transition(event, GateEvent.Status.REJECTED)

        now = timezone.now()
        event.status = GateEvent.Status.REJECTED
        event.event_type = GateEvent.EventType.REJECTED
        event.save()

        GateEventLifecycleService._record_approval_decision(
            event=event,
            decision=GateEventApproval.Decision.REJECTED,
            decided_by=decided_by,
            decided_at=now,
            method="",
            notes=reason,
        )

        after = GateEventLifecycleService._serialize(event)
        GateEventLifecycleService._log_audit(
            event,
            GateOpsAuditLog.Action.REJECT,
            before,
            after,
            actor=decided_by,
        )
        return event

    @staticmethod
    def record_entry(event, guard=None):
        """Transition: approved → entered.

        Sets ``entered_at`` and schedules ``auto_close_at`` using the society's
        ``GateOpsSocietyConfig.auto_close_after_hours`` (default 24h when no
        config exists; ``None`` when the society has disabled auto-close).
        """
        before = GateEventLifecycleService._serialize(event)
        GateEventLifecycleService._validate_transition(event, GateEvent.Status.ENTERED)

        now = timezone.now()
        event.status = GateEvent.Status.ENTERED
        event.entered_at = now
        if guard is not None:
            event.guard = guard

        hours = GateEventLifecycleService._get_auto_close_hours(event.society)
        event.auto_close_at = now + timedelta(hours=hours) if hours else None
        event.save()

        after = GateEventLifecycleService._serialize(event)
        GateEventLifecycleService._log_audit(
            event,
            GateOpsAuditLog.Action.ENTRY,
            before,
            after,
            actor=None,
        )
        return event

    @staticmethod
    def record_exit(event, guard=None):
        """Transition: entered → exited. Sets ``exited_at``."""
        before = GateEventLifecycleService._serialize(event)
        GateEventLifecycleService._validate_transition(event, GateEvent.Status.EXITED)

        now = timezone.now()
        event.status = GateEvent.Status.EXITED
        event.exited_at = now
        if guard is not None:
            event.guard = guard
        event.save()

        after = GateEventLifecycleService._serialize(event)
        GateEventLifecycleService._log_audit(
            event,
            GateOpsAuditLog.Action.EXIT,
            before,
            after,
            actor=None,
        )
        return event

    @staticmethod
    def auto_close(event):
        """Transition: entered → auto_closed.

        Sets ``exited_at`` (for duration calculation) and
        ``event_type=auto_close``. The audit ``after_value`` records an
        overstay flag when the event ran past its scheduled ``auto_close_at``.
        """
        before = GateEventLifecycleService._serialize(event)
        GateEventLifecycleService._validate_transition(event, GateEvent.Status.AUTO_CLOSED)

        now = timezone.now()
        event.status = GateEvent.Status.AUTO_CLOSED
        event.exited_at = now
        event.event_type = GateEvent.EventType.AUTO_CLOSE
        event.save()

        after = GateEventLifecycleService._serialize(event)
        if event.auto_close_at is not None and event.auto_close_at < now:
            after["overstay"] = True
            after["overstay_by_minutes"] = int(
                (now - event.auto_close_at).total_seconds() // 60
            )

        GateEventLifecycleService._log_audit(
            event,
            GateOpsAuditLog.Action.STATE_TRANSITION,
            before,
            after,
            actor=None,
        )
        return event

    @staticmethod
    def cancel(event, cancelled_by, reason=""):
        """Transition: invited → cancelled. Sets ``event_type=cancelled``."""
        before = GateEventLifecycleService._serialize(event)
        GateEventLifecycleService._validate_transition(event, GateEvent.Status.CANCELLED)

        if reason:
            event.notes = (event.notes + "\n" + reason).strip() if event.notes else reason
        event.status = GateEvent.Status.CANCELLED
        event.event_type = GateEvent.EventType.CANCELLED
        event.save()

        after = GateEventLifecycleService._serialize(event)
        GateEventLifecycleService._log_audit(
            event,
            GateOpsAuditLog.Action.STATE_TRANSITION,
            before,
            after,
            actor=cancelled_by,
        )
        return event

    @staticmethod
    def expire(event, reason=""):
        """Transition: invited → expired OR arrived → expired.

        Sets ``event_type=expired``. Used for pass expiry (from invited) and
        approval timeout (from arrived).
        """
        before = GateEventLifecycleService._serialize(event)
        GateEventLifecycleService._validate_transition(event, GateEvent.Status.EXPIRED)

        if reason:
            event.notes = (event.notes + "\n" + reason).strip() if event.notes else reason
        event.status = GateEvent.Status.EXPIRED
        event.event_type = GateEvent.EventType.EXPIRED
        event.save()

        after = GateEventLifecycleService._serialize(event)
        GateEventLifecycleService._log_audit(
            event,
            GateOpsAuditLog.Action.STATE_TRANSITION,
            before,
            after,
            actor=None,
        )
        return event

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _apply_transition(event, new_status, *, action, actor=None, extra_fields=None):
        """Validate, apply, save, and audit a state transition.

        This is the shared spine of every transition: capture before → validate
        → mutate → save → capture after → audit. Callers pass method-specific
        fields via ``extra_fields``.
        """
        before = GateEventLifecycleService._serialize(event)
        GateEventLifecycleService._validate_transition(event, new_status)

        event.status = new_status
        if extra_fields:
            for key, value in extra_fields.items():
                setattr(event, key, value)
        event.save()

        after = GateEventLifecycleService._serialize(event)
        GateEventLifecycleService._log_audit(event, action, before, after, actor=actor)
        return event

    @staticmethod
    def _validate_transition(event, new_status):
        """Raise ``ValidationError`` if ``event.status → new_status`` is illegal."""
        allowed = GateEventLifecycleService._TRANSITIONS.get(event.status, set())
        if new_status not in allowed:
            raise ValidationError(
                f"Illegal state transition: {event.status!r} → {new_status!r} "
                f"for GateEvent {event.pk}."
            )

    @staticmethod
    def _serialize(event):
        """Return a JSON-safe dict of the event's key fields for audit logging."""
        def _dt(value):
            return value.isoformat() if value else None

        return {
            "status": event.status,
            "event_type": event.event_type,
            "arrived_at": _dt(event.arrived_at),
            "approved_at": _dt(event.approved_at),
            "entered_at": _dt(event.entered_at),
            "exited_at": _dt(event.exited_at),
            "auto_close_at": _dt(event.auto_close_at),
            "rule_action": event.rule_action,
        }

    @staticmethod
    def _log_audit(event, action, before, after, actor=None):
        """Write an append-only GateOpsAuditLog entry for a transition.

        Wrapped so a logging failure never blocks a legitimate gate operation;
        the error is logged at ERROR level instead.
        """
        try:
            GateOpsAuditLog.log(
                society=event.society,
                action=action,
                entity_type="GateEvent",
                entity_id=str(event.pk),
                actor=actor,
                before_value=before,
                after_value=after,
            )
        except Exception:  # noqa: BLE001 — audit must not break the transition.
            logger.exception(
                "Failed to write GateOpsAuditLog for GateEvent %s (action=%s)",
                event.pk,
                action,
            )

    @staticmethod
    def _create_approval_request(event, notes=""):
        """Create a pending GateEventApproval row for the event."""
        return GateEventApproval.objects.create(
            gate_event=event,
            society=event.society,
            decision=GateEventApproval.Decision.PENDING,
            notes=notes,
        )

    @staticmethod
    def _record_approval_decision(
        event,
        decision,
        decided_by,
        decided_at,
        method="",
        notes="",
    ):
        """Update the latest pending approval row, or create a decided one.

        If a pending approval exists it is resolved in place; otherwise a new
        approval row is created already in the decided state (covers auto-approve
        / direct-entry flows that never created a pending request).
        """
        approval = (
            GateEventApproval.objects.filter(
                gate_event=event,
                decision=GateEventApproval.Decision.PENDING,
            )
            .order_by("-requested_at")
            .first()
        )
        if approval is not None:
            approval.decision = decision
            approval.decided_by = decided_by
            approval.decided_at = decided_at
            if method:
                approval.decision_method = method
            if notes:
                approval.notes = notes
            approval.save()
            return approval

        return GateEventApproval.objects.create(
            gate_event=event,
            society=event.society,
            decision=decision,
            decided_by=decided_by,
            decided_at=decided_at,
            decision_method=method,
            notes=notes,
        )

    @staticmethod
    def _get_auto_close_hours(society):
        """Return auto-close hours for the society.

        - No config row → ``DEFAULT_AUTO_CLOSE_HOURS`` (24).
        - Config exists but ``auto_close_enabled=False`` → ``None`` (disabled).
        - Otherwise → ``config.auto_close_after_hours``.
        """
        try:
            config = GateOpsSocietyConfig.objects.filter(society=society).first()
        except Exception:  # noqa: BLE001 — never block entry on a config lookup.
            logger.exception(
                "Failed to load GateOpsSocietyConfig for society %s; "
                "defaulting to %s hours.",
                society,
                DEFAULT_AUTO_CLOSE_HOURS,
            )
            return DEFAULT_AUTO_CLOSE_HOURS

        if config is None:
            return DEFAULT_AUTO_CLOSE_HOURS
        if not config.auto_close_enabled:
            return None
        return config.auto_close_after_hours

    @staticmethod
    def _build_rule_context(event):
        """Build the context dict consumed by ``RuleEngineService.evaluate``."""
        person = event.person
        visitor_category = event.visitor_category
        return {
            "society": event.society,
            "society_id": event.society_id,
            "direction": event.direction,
            "applies_on": (
                Rule.AppliesOn.ENTRY
                if event.direction == GateEvent.Direction.INBOUND
                else Rule.AppliesOn.EXIT
            ),
            "date": timezone.localdate(),
            "visitor_category": visitor_category.code if visitor_category else None,
            "visitor_category_id": event.visitor_category_id,
            "visitor_category_code": visitor_category.code if visitor_category else None,
            "gate": event.gate_id,
            "gate_id": event.gate_id,
            "guard": event.guard_id,
            "vehicle": event.vehicle_id,
            "vehicle_id": event.vehicle_id,
            "person": {
                "is_blacklisted": bool(person and person.is_blacklisted),
                "is_vip": bool(person and person.is_vip),
            },
            "is_vip": bool(person and person.is_vip),
            "is_blacklisted": bool(person and person.is_blacklisted),
            "created_by": event.created_by,
            "actor": event.created_by,
            "gate_event_id": event.pk,
        }
