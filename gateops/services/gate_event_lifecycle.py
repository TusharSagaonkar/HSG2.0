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
    GateVehicle,
    NotificationPreference,
    Rule,
    RuleAction,
    WorkPermit,
)
from gateops.services.ai_recommendation_service import AIRecommendationService
from gateops.services.notification_engine import NotificationEngineService
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
        gate_vehicle=None,
    ):
        """Create a GateEvent with status=invited, event_type=invitation.

        ``gate`` is required by the ``GateEvent`` model (non-nullable FK); it is
        accepted as a keyword for ergonomic call sites but a ``None`` gate
        raises ``ValidationError`` rather than failing later at the DB layer.

        ``gate_vehicle`` optionally links a visitor/non-resident vehicle
        (:class:`GateVehicle`) to the event. It may be passed as a
        ``GateVehicle`` instance or its primary key; it is validated to belong
        to the same society as the event. This is distinct from the resident
        ``vehicle`` FK (``parking.Vehicle``), which is left untouched.
        """
        if gate is None:
            raise ValidationError({"gate": "gate is required to create an invitation."})

        gate_vehicle = GateEventLifecycleService._resolve_gate_vehicle(
            gate_vehicle, society
        )

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
            gate_vehicle=gate_vehicle,
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
    def record_arrival(event, gate, guard=None, photo_url="", gate_vehicle=None):
        """Transition: invited → arrived.

        Sets ``arrived_at``, ``gate``, ``guard``, ``photo_url`` and then runs
        :meth:`evaluate_rules` to drive the next state based on configured
        rules. Walk-in arrivals (``event is None``) are not supported by this
        signature — create an invitation (or an arrived event) first.

        ``gate_vehicle`` optionally links a visitor/non-resident vehicle
        (:class:`GateVehicle`) to the event at arrival time. It may be passed
        as a ``GateVehicle`` instance or its primary key; it is validated to
        belong to the same society as the event. This is distinct from the
        resident ``vehicle`` FK (``parking.Vehicle``), which is left untouched.
        """
        if event is None:
            raise ValidationError(
                "record_arrival requires an existing invited GateEvent; "
                "create one via create_invitation() first."
            )

        before = GateEventLifecycleService._serialize(event)
        GateEventLifecycleService._validate_transition(event, GateEvent.Status.ARRIVED)

        gate_vehicle = GateEventLifecycleService._resolve_gate_vehicle(
            gate_vehicle, event.society
        )

        now = timezone.now()
        event.status = GateEvent.Status.ARRIVED
        event.arrived_at = now
        event.gate = gate
        if gate_vehicle is not None:
            event.gate_vehicle = gate_vehicle
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

        # Phase 10: notify the host that a visitor has arrived. Fired after
        # rule evaluation so the event's final status (auto-approved, pending
        # approval, etc.) is reflected in template selection. Non-blocking.
        GateEventLifecycleService._notify(
            event, NotificationPreference.Trigger.ARRIVAL, actor=guard
        )
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
            # Dispatch notifications for rule-action types, then surface to a
            # human approver (safe middle ground). The approval request is
            # still created so the existing "safe default" behaviour is
            # preserved; notifications are an additional side-effect.
            for action in result.actions:
                if action.action in (
                    RuleAction.ActionType.SEND_NOTIFICATION,
                    RuleAction.ActionType.NOTIFY_SECURITY,
                    RuleAction.ActionType.ESCALATE,
                ):
                    try:
                        NotificationEngineService.dispatch_for_rule_action(
                            event=event,
                            action=action.action,
                            parameters=action.parameters,
                            actor=None,
                        )
                    except Exception:  # noqa: BLE001 — never block gate ops.
                        logger.exception(
                            "Rule action notification failed for event %s, "
                            "action %s",
                            event.pk,
                            action.action,
                        )
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

        # Phase 10: notify the host of the approval (part of the arrival
        # flow). Template selection distinguishes approval_request from
        # visitor_arrival based on the event's current state. Non-blocking.
        GateEventLifecycleService._notify(
            event, NotificationPreference.Trigger.ARRIVAL, actor=approved_by
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

        # Phase 10: notify the host that the visitor has entered. Non-blocking.
        GateEventLifecycleService._notify(
            event, NotificationPreference.Trigger.ENTRY, actor=guard
        )

        # Phase 11: real-time anomaly check. Fired AFTER the transition is
        # committed, audited, and notified so that an AI failure never blocks
        # or rolls back the entry. The check itself is non-blocking: any
        # exception is logged and swallowed. Anomalies detected here are
        # persisted by AIRecommendationService and dispatched via the ANOMALY
        # notification trigger.
        try:
            AIRecommendationService._check_entry_anomalies(event=event)
        except Exception:  # noqa: BLE001 — AI must never block gate ops.
            logger.warning(
                "AI anomaly check failed for GateEvent %s; entry not blocked.",
                event.pk,
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

        # Phase 10: notify the host that the visitor has exited. Non-blocking.
        GateEventLifecycleService._notify(
            event, NotificationPreference.Trigger.EXIT, actor=guard
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

        # Phase 10: notify the host of the auto-close (an exit-like event).
        # Uses the EXIT trigger; the engine selects the auto_close template
        # based on the event's AUTO_CLOSED status. Non-blocking.
        GateEventLifecycleService._notify(
            event, NotificationPreference.Trigger.EXIT, actor=None
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
    def _notify(event, trigger, actor=None):
        """Dispatch a notification for a gate event transition.

        Wrapped in try/except so notification failures NEVER block gate
        operations (mirrors the :meth:`_log_audit` robustness pattern). The
        underlying :meth:`NotificationEngineService.dispatch_for_event` is
        itself wrapped, but this guard ensures that even import-time or
        signature errors cannot propagate into the lifecycle.

        ``actor`` may be a ``User`` or a ``SecurityGuard``. When a guard is
        passed, its linked ``user`` (if any) is used as the audit-log actor
        because :class:`GateOpsAuditLog.actor` is a FK to ``User``.
        """
        try:
            audit_actor = actor
            # SecurityGuard is not a User subclass; resolve the linked user
            # so GateOpsAuditLog.actor (FK to User) doesn't reject the value.
            if actor is not None and not hasattr(actor, "is_authenticated"):
                audit_actor = getattr(actor, "user", None)
            NotificationEngineService.dispatch_for_event(
                event=event, trigger=trigger, actor=audit_actor
            )
        except Exception:  # noqa: BLE001 — notifications must not break gates.
            logger.exception(
                "Notification dispatch failed for event %s, trigger %s",
                event.pk,
                trigger,
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
    def _resolve_gate_vehicle(gate_vehicle, society):
        """Resolve and validate a ``gate_vehicle`` argument.

        Accepts either a :class:`GateVehicle` instance or its primary key. When
        ``None`` is passed (the default), ``None`` is returned unchanged so
        callers that omit the argument are unaffected.

        The resolved vehicle is validated to belong to ``society``; a mismatch
        raises ``ValueError`` so cross-society linkage is impossible. A
        non-existent ID raises ``ValueError`` as well (rather than a raw
        ``DoesNotExist``) for a clean, predictable caller contract.
        """
        if gate_vehicle is None:
            return None

        if isinstance(gate_vehicle, GateVehicle):
            gv = gate_vehicle
        else:
            try:
                gv = GateVehicle.objects.get(pk=gate_vehicle)
            except GateVehicle.DoesNotExist as exc:
                raise ValueError(
                    f"GateVehicle with id={gate_vehicle} does not exist."
                ) from exc

        if gv.society_id != society.pk:
            raise ValueError(
                "GateVehicle society mismatch: gate_vehicle belongs to "
                f"society_id={gv.society_id} but the event belongs to "
                f"society_id={society.pk}."
            )
        return gv

    @staticmethod
    def _build_rule_context(event):
        """Build the context dict consumed by ``RuleEngineService.evaluate``."""
        person = event.person
        visitor_category = event.visitor_category
        gv = event.gate_vehicle
        # Guard the cached vehicle_category relation: if the FK is null or the
        # related row is missing, fall back to None instead of raising.
        gv_category = getattr(gv, "vehicle_category", None) if gv else None
        context = {
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
            # Phase 6: visitor/non-resident vehicle context. All keys safely
            # default to None/False when no gate_vehicle is linked so existing
            # rules that ignore these keys are unaffected.
            "gate_vehicle": gv,
            "gate_vehicle_id": gv.pk if gv else None,
            "gate_vehicle_number": gv.vehicle_number if gv else None,
            "gate_vehicle_category": gv.vehicle_category_id if gv else None,
            "gate_vehicle_category_name": gv_category.name if gv_category else None,
            "gate_vehicle_is_watchlisted": bool(gv.is_watchlisted) if gv else False,
            "gate_vehicle_is_repeat": bool(gv.is_repeat) if gv else False,
        }

        # Phase 9: Contractor expiry context. Populated when the event's
        # visitor category is a contractor category OR a contractor/contract FK
        # is directly linked. The rule engine maps CONTRACTOR_EXPIRY conditions
        # to this key; when no contractor context exists the value is None so
        # IS_FALSE conditions match (no expiry concern) and IS_TRUE do not.
        context["contractor_expiry"] = (
            GateEventLifecycleService._build_contractor_expiry_context(event)
        )

        # Phase 11: cached risk score for RISK_SCORE condition evaluation.
        # Uses the lightweight VisitorPattern.risk_score read (no recomputation)
        # so rule evaluation stays fast. Defaults to 0.0 on any failure so
        # risk-based rules degrade to the safest (lowest-risk) outcome.
        try:
            context["risk_score"] = AIRecommendationService._get_cached_risk_score(
                society=event.society, person=person
            )
        except Exception:  # noqa: BLE001 — rule context must always build.
            context["risk_score"] = 0.0
        return context

    @staticmethod
    def _build_contractor_expiry_context(event):
        """Build the ``contractor_expiry`` context value for the rule engine.

        Returns ``None`` when the event has no contractor context (neither a
        contractor-category visitor nor a linked contractor/contract FK).
        Otherwise returns a dict describing the contract and work-permit expiry
        state:

        - ``contract_expired``: ``contract.end_date < today``
        - ``permit_expired``: ``work_permit.expires_at < now``
        - ``days_until_contract_expiry``: integer days (negative if expired)
        - ``days_until_permit_expiry``: integer days (negative if expired)
        - ``has_active_permit``: whether an ACTIVE work permit is linked/found
        """
        visitor_category = event.visitor_category
        is_contractor_category = bool(
            visitor_category
            and getattr(visitor_category, "is_contractor", False)
        )
        contractor = event.contractor
        contract = event.contract

        # No contractor context at all → None. The rule engine treats a None
        # value as "field absent": IS_FALSE conditions match (no expiry
        # concern), IS_TRUE conditions do not.
        if not is_contractor_category and contractor is None and contract is None:
            return None

        now = timezone.now()
        today = now.date()

        # Resolve the contract: prefer the event's direct FK. When only the
        # visitor category flags contractor but no contract is linked, we still
        # return a context dict (with contract fields as None) so rules can
        # distinguish "contractor but no contract" from "not a contractor".
        contract_expired = False
        days_until_contract_expiry = None
        if contract is not None and contract.end_date is not None:
            contract_expired = contract.end_date < today
            days_until_contract_expiry = (contract.end_date - today).days

        # Resolve the most recent active work permit for the contract. Prefer
        # the event's direct work_permit FK; fall back to a lookup on the
        # contract's active permits (most-recently-expiring first).
        work_permit = event.work_permit
        if work_permit is None and contract is not None:
            work_permit = (
                WorkPermit.objects.filter(
                    society=event.society,
                    contract=contract,
                    status=WorkPermit.Status.ACTIVE,
                    is_active=True,
                )
                .order_by("-expires_at")
                .first()
            )

        permit_expired = False
        days_until_permit_expiry = None
        has_active_permit = False
        if work_permit is not None and work_permit.expires_at is not None:
            has_active_permit = work_permit.status == WorkPermit.Status.ACTIVE
            permit_expired = work_permit.expires_at < now
            days_until_permit_expiry = (work_permit.expires_at - now).days

        return {
            "contract_expired": contract_expired,
            "permit_expired": permit_expired,
            "days_until_contract_expiry": days_until_contract_expiry,
            "days_until_permit_expiry": days_until_permit_expiry,
            "has_active_permit": has_active_permit,
        }
