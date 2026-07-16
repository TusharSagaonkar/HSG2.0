"""Service layer for the Smart Notification Engine (Phase 10).

This service is the single authority over gate-event notification routing,
bundling, and dispatch. No caller should create :class:`NotificationBundle`
rows directly — every notification operation must flow through
:class:`NotificationEngineService` so that:

1. Multi-tenant safety is enforced (every query scoped by ``society``).
2. The host (recipient) is resolved consistently from the event's
   ``host_unit`` via occupancy → ownership → member fallback.
3. Notification preferences (channel, trigger, silent mode, bundling
   window) are honoured — the "no spam" philosophy.
4. Repeat-suppression prevents burst notifications for the same visitor.
5. Bundling groups notifications for the same host unit within a
   configurable time window.
6. A :class:`GateOpsAuditLog` entry is written (append-only).

Design notes
------------
- **Never block gate operations:** the main entry point
  :meth:`dispatch_for_event` is wrapped in try/except so a notification
  failure never prevents a gate transition (matching the ``_log_audit``
  robustness philosophy from :mod:`parcel_service`).
- **Channel readiness:** only ``Channel.EMAIL`` has delivery infrastructure
  (via :func:`queue_email`). Push/SMS/WhatsApp/Voice channels create a
  PENDING bundle and log a warning — a placeholder until the transport
  layer is built.
- **Audit robustness:** audit-log writes are wrapped so a logging failure
  never blocks a legitimate notification operation (the error is logged
  loudly instead).
- **All methods are ``@staticmethod``** per the service contract; there is
  no shared mutable state.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import QuerySet
from django.shortcuts import get_object_or_404
from django.utils import timezone

from gateops.models import (
    GateEvent,
    GateOpsAuditLog,
    NotificationBundle,
    NotificationPreference,
    RuleAction,
)
from housing.models import Member, UnitOccupancy, UnitOwnership
from notifications.models import EmailQueue
from notifications.services import queue_email

logger = logging.getLogger(__name__)

# Default duplicate-suppression window (minutes). Prevents the same visitor
# from triggering repeated notifications within a short burst of gate activity
# (e.g. multiple scans of the same pass at the same gate).
DEFAULT_DUPLICATE_WINDOW_MINUTES = 5


class NotificationEngineService:
    """Service for smart notification routing, bundling, and dispatch.

    Every notification operation:
    1. Validates multi-tenant safety (society scoping).
    2. Resolves the host (recipient) from the event's ``host_unit``.
    3. Honours notification preferences (channel, trigger, silent, bundle).
    4. Applies repeat-suppression and bundling.
    5. Dispatches via the appropriate channel.
    6. Creates a GateOpsAuditLog entry.
    """

    # ------------------------------------------------------------------ #
    # 1. Host Resolution
    # ------------------------------------------------------------------ #

    @staticmethod
    def resolve_host(*, event) -> dict | None:
        """Resolve who should be notified for a gate event.

        Resolution order (first match wins):
        1. ``UnitOccupancy`` — active occupancy (``end_date__isnull=True``).
           If TENANT, the tenant is the recipient; if OWNER, the
           owner-occupant is the recipient.
        2. ``UnitOwnership`` — active PRIMARY owner
           (``end_date__isnull=True``).
        3. ``Member`` — active member (``status=ACTIVE``,
           ``end_date__isnull=True``) ordered by role (OWNER first).

        Returns ``None`` when ``event.host_unit`` is not set (the host
        cannot be resolved without a unit anchor).

        Returns a dict::

            {"user": User | None, "email": str, "phone": str,
             "name": str, "unit": Unit}
        """
        unit = event.host_unit
        if unit is None:
            return None

        # 1. Active occupancy — prefer the current occupant (tenant or
        #    owner-occupant). VACANT occupancies have no occupant and are
        #    skipped.
        occupancy = (
            UnitOccupancy.objects.filter(
                unit=unit,
                end_date__isnull=True,
            )
            .exclude(occupancy_type=UnitOccupancy.OccupancyType.VACANT)
            .select_related("occupant")
            .order_by("-start_date")
            .first()
        )
        if occupancy is not None and occupancy.occupant is not None:
            contact = NotificationEngineService._resolve_user_contact(
                user=occupancy.occupant, unit=unit
            )
            return {
                "user": occupancy.occupant,
                "email": contact["email"],
                "phone": contact["phone"],
                "name": contact["name"],
                "unit": unit,
            }

        # 2. Active PRIMARY owner.
        ownership = (
            UnitOwnership.objects.filter(
                unit=unit,
                role=UnitOwnership.OwnershipRole.PRIMARY,
                end_date__isnull=True,
            )
            .select_related("owner")
            .first()
        )
        if ownership is not None and ownership.owner is not None:
            contact = NotificationEngineService._resolve_user_contact(
                user=ownership.owner, unit=unit
            )
            return {
                "user": ownership.owner,
                "email": contact["email"],
                "phone": contact["phone"],
                "name": contact["name"],
                "unit": unit,
            }

        # 3. Active member for this unit, ordered by role (OWNER first).
        member = (
            Member.objects.filter(
                unit=unit,
                status=Member.MemberStatus.ACTIVE,
                end_date__isnull=True,
            )
            .order_by("role")
            .first()
        )
        if member is not None:
            return {
                "user": member.user,
                "email": member.email or (
                    member.user.email if member.user else ""
                ),
                "phone": member.phone,
                "name": member.full_name,
                "unit": unit,
            }

        # No host could be resolved from any source.
        return None

    @staticmethod
    def _resolve_user_contact(*, user, unit) -> dict:
        """Resolve email/phone/name for a User, preferring an active Member.

        The ``UnitOccupancy`` / ``UnitOwnership`` models link to a ``User``
        (not a ``Member``), so contact details are not directly available.
        This helper looks up an active :class:`Member` for the same user +
        unit to obtain ``email`` / ``phone`` / ``full_name``. When no Member
        row exists, it falls back to the User's own attributes.
        """
        member = (
            Member.objects.filter(
                user=user,
                unit=unit,
                status=Member.MemberStatus.ACTIVE,
                end_date__isnull=True,
            )
            .first()
        )
        if member is not None:
            return {
                "email": member.email or getattr(user, "email", ""),
                "phone": member.phone,
                "name": member.full_name,
            }
        # Fallback to User attributes. ``get_full_name()`` may return an
        # empty string when first_name/last_name are unset; fall back to the
        # username so the email context always has a non-empty name.
        full_name = getattr(user, "get_full_name", lambda: "")() or ""
        return {
            "email": getattr(user, "email", ""),
            "phone": "",
            "name": full_name or getattr(user, "get_username", lambda: "")(),
        }

    # ------------------------------------------------------------------ #
    # 2. Preference Resolution
    # ------------------------------------------------------------------ #

    @staticmethod
    def get_preferences(*, society, visitor_category) -> QuerySet:
        """Return all active NotificationPreference rows for the society +
        visitor_category.

        Ordered by the model's default (``society``, ``visitor_category``,
        ``channel``) so callers see a stable, deterministic ordering.
        """
        return NotificationPreference.objects.filter(
            society=society,
            visitor_category=visitor_category,
            is_active=True,
        ).order_by("society", "visitor_category", "channel")

    @staticmethod
    def get_preference_for_trigger(
        *, society, visitor_category, trigger
    ) -> NotificationPreference | None:
        """Return the first active preference matching the given trigger.

        If no preference exists for this trigger, ``None`` is returned (no
        notification should be sent for this trigger).
        """
        return (
            NotificationPreference.objects.filter(
                society=society,
                visitor_category=visitor_category,
                trigger=trigger,
                is_active=True,
            )
            .order_by("society", "visitor_category", "channel")
            .first()
        )

    # ------------------------------------------------------------------ #
    # 3. Smart Routing
    # ------------------------------------------------------------------ #

    @staticmethod
    def dispatch_for_event(
        *, event, trigger, actor=None
    ) -> NotificationBundle | None:
        """The MAIN entry point — route a gate event to the right recipient.

        Called by lifecycle hooks (arrival / entry / exit). Steps:

        1. Resolve the preference for the event's society + visitor_category
           + trigger. If none exists, no notification is sent.
        2. Suppress if the preference channel is NONE or trigger is NEVER.
        3. If the preference is in silent mode, create a SKIPPED bundle for
           traceability and return it.
        4. Repeat-suppression: if a notification was already sent for this
           person + trigger within the duplicate window, skip silently.
        5. Resolve the host (recipient). If no host can be resolved, create
           a SKIPPED bundle and return it.
        6. Find or create a bundle (honouring the bundling window).
        7. Dispatch via the appropriate channel. For EMAIL with no bundling
           window, dispatch immediately. For EMAIL with a bundling window,
           leave the bundle PENDING (flushed later by
           :meth:`flush_pending_bundles`). For other channels, leave PENDING
           and log a warning (no transport infrastructure yet).
        8. Audit the dispatch.

        The entire method is wrapped in try/except so notification failures
        NEVER block gate operations. On exception, the error is logged and
        ``None`` is returned.
        """
        try:
            society = event.society
            visitor_category = event.visitor_category

            # 1. Resolve the preference for this trigger.
            preference = NotificationEngineService.get_preference_for_trigger(
                society=society,
                visitor_category=visitor_category,
                trigger=trigger,
            )
            if preference is None:
                # No preference configured for this trigger — no notification.
                return None

            # 2. Suppressed channels / triggers.
            if (
                preference.channel == NotificationPreference.Channel.NONE
                or preference.trigger == NotificationPreference.Trigger.NEVER
            ):
                return None

            # 3. Silent mode — create a SKIPPED bundle for traceability.
            if preference.is_silent:
                bundle = NotificationEngineService._create_skipped_bundle(
                    event=event,
                    preference=preference,
                    trigger=trigger,
                    reason="silent",
                )
                NotificationEngineService._log_audit(
                    society=society,
                    action=GateOpsAuditLog.Action.STATE_TRANSITION,
                    bundle=bundle,
                    before=None,
                    after={"status": "skipped", "reason": "silent"},
                    actor=actor,
                )
                return bundle

            # 4. Repeat-suppression — skip if the same person was already
            #    notified for this trigger within the duplicate window.
            if (
                event.person is not None
                and NotificationEngineService._is_duplicate_notification(
                    event=event, trigger=trigger
                )
            ):
                logger.debug(
                    "Suppressing duplicate notification for person %s "
                    "(trigger=%s, event=%s).",
                    event.person_id,
                    trigger,
                    event.pk,
                )
                return None

            # 5. Resolve the host (recipient).
            host = NotificationEngineService.resolve_host(event=event)
            if host is None:
                bundle = NotificationEngineService._create_skipped_bundle(
                    event=event,
                    preference=preference,
                    trigger=trigger,
                    reason="no_host",
                )
                NotificationEngineService._log_audit(
                    society=society,
                    action=GateOpsAuditLog.Action.STATE_TRANSITION,
                    bundle=bundle,
                    before=None,
                    after={"status": "skipped", "reason": "no_host"},
                    actor=actor,
                )
                return bundle

            # 6. Find or create a bundle (honouring the bundling window).
            bundle = NotificationEngineService._find_or_create_bundle(
                event=event,
                preference=preference,
                trigger=trigger,
                host=host,
            )

            # 7. Dispatch via the appropriate channel.
            #
            # When bundling is disabled (window == 0), dispatch immediately.
            # When bundling is enabled (window > 0), leave the bundle PENDING
            # — it will be flushed by ``flush_pending_bundles`` once the
            # window elapses.
            dispatch_now = preference.bundle_window_minutes == 0

            if preference.channel == NotificationPreference.Channel.EMAIL:
                if dispatch_now:
                    email_queue = NotificationEngineService._dispatch_email(
                        event=event,
                        host=host,
                        preference=preference,
                        trigger=trigger,
                        bundle=bundle,
                    )
                    if email_queue is not None:
                        bundle.email_queue = email_queue
                    bundle.status = NotificationBundle.Status.SENT
                    bundle.dispatched_at = timezone.now()
                    bundle.save()
                # else: leave PENDING for ``flush_pending_bundles``.
            else:
                # Non-email channels (PUSH/SMS/WHATSAPP/VOICE) have no
                # transport infrastructure yet. Create the bundle as PENDING
                # and log a warning so the gap is visible in production logs.
                logger.warning(
                    "Notification channel '%s' has no dispatch infrastructure; "
                    "bundle %s left PENDING (event=%s, trigger=%s).",
                    preference.channel,
                    bundle.pk,
                    event.pk,
                    trigger,
                )

            # 8. Audit.
            NotificationEngineService._log_audit(
                society=society,
                action=GateOpsAuditLog.Action.CREATE,
                bundle=bundle,
                before=None,
                after=NotificationEngineService._serialize_bundle(bundle),
                actor=actor,
            )
            return bundle

        except Exception:  # noqa: BLE001 — never block gate operations.
            logger.exception(
                "Notification dispatch failed for event %s (trigger=%s). "
                "The gate operation will proceed normally.",
                getattr(event, "pk", None),
                trigger,
            )
            return None

    # ------------------------------------------------------------------ #
    # 4. Email Dispatch
    # ------------------------------------------------------------------ #

    @staticmethod
    def _dispatch_email(
        *,
        event,
        host,
        preference,
        trigger,
        bundle=None,
        template_name: str | None = None,
    ) -> EmailQueue | None:
        """Build the email context and queue a notification email.

        - Selects the template based on the trigger (overridable via
          ``template_name`` for rule-action dispatch).
        - Calls :func:`queue_email` with ``EmailType.NOTICE``.
        - If the host email is empty, logs a warning and returns ``None``.

        Returns the created :class:`EmailQueue` instance, or ``None`` if no
        email could be queued.
        """
        recipient_email = host.get("email", "") if host else ""
        if not recipient_email:
            logger.warning(
                "Cannot dispatch email: host has no email address "
                "(event=%s, trigger=%s).",
                getattr(event, "pk", None),
                trigger,
            )
            return None

        context = NotificationEngineService._build_email_context(
            event=event, host=host
        )
        resolved_template = NotificationEngineService._select_template(
            event=event, trigger=trigger, template_name=template_name
        )

        return queue_email(
            recipient_email=recipient_email,
            society=event.society,
            template_name=resolved_template,
            context=context,
            email_type=EmailQueue.EmailType.NOTICE,
        )

    # ------------------------------------------------------------------ #
    # 5. Bundling
    # ------------------------------------------------------------------ #

    @staticmethod
    @transaction.atomic
    def _find_or_create_bundle(
        *, event, preference, trigger, host
    ) -> NotificationBundle:
        """Find an existing PENDING bundle within the window, or create one.

        - If ``preference.bundle_window_minutes == 0``: create a new bundle
          immediately (no bundling).
        - If ``preference.bundle_window_minutes > 0``: look for an existing
          PENDING bundle for the same society + visitor_category +
          host_unit + trigger + channel created within the window. If found,
          add the event to it and return the existing bundle. Otherwise
          create a new PENDING bundle.

        The event is always added to the bundle's ``gate_events`` M2M.
        """
        recipient_email = host.get("email", "") if host else ""

        if preference.bundle_window_minutes > 0:
            window_start = timezone.now() - timedelta(
                minutes=preference.bundle_window_minutes
            )
            existing = (
                NotificationBundle.objects.select_for_update()
                .filter(
                    society=event.society,
                    visitor_category=event.visitor_category,
                    host_unit=event.host_unit,
                    trigger=trigger,
                    channel=preference.channel,
                    status=NotificationBundle.Status.PENDING,
                    is_active=True,
                    created_at__gte=window_start,
                )
                .first()
            )
            if existing is not None:
                existing.gate_events.add(event)
                return existing

        bundle = NotificationBundle(
            society=event.society,
            visitor_category=event.visitor_category,
            host_unit=event.host_unit,
            trigger=trigger,
            channel=preference.channel,
            recipient_email=recipient_email,
            bundle_window_minutes=preference.bundle_window_minutes,
            status=NotificationBundle.Status.PENDING,
        )
        bundle.save()
        bundle.gate_events.add(event)
        return bundle

    @staticmethod
    @transaction.atomic
    def flush_bundle(*, bundle, actor=None) -> NotificationBundle:
        """Dispatch a PENDING bundle (send the accumulated notifications).

        - If the channel is EMAIL, resolves the host from the bundle's first
          event and calls :meth:`_dispatch_email`.
        - Sets ``status=SENT`` and ``dispatched_at=now()``.
        - Audits the dispatch.

        Returns the refreshed bundle.
        """
        if bundle.status != NotificationBundle.Status.PENDING:
            return bundle

        events = list(bundle.gate_events.all().order_by("created_at"))
        if not events:
            # No events to dispatch — mark as SKIPPED.
            bundle.status = NotificationBundle.Status.SKIPPED
            bundle.save()
            NotificationEngineService._log_audit(
                society=bundle.society,
                action=GateOpsAuditLog.Action.STATE_TRANSITION,
                bundle=bundle,
                before={"status": "pending"},
                after={"status": "skipped", "reason": "no_events"},
                actor=actor,
            )
            return bundle

        first_event = events[0]

        if bundle.channel == NotificationPreference.Channel.EMAIL:
            host = NotificationEngineService.resolve_host(event=first_event)
            preference = NotificationEngineService.get_preference_for_trigger(
                society=bundle.society,
                visitor_category=bundle.visitor_category,
                trigger=bundle.trigger,
            )
            email_queue = NotificationEngineService._dispatch_email(
                event=first_event,
                host=host,
                preference=preference,
                trigger=bundle.trigger,
                bundle=bundle,
            )
            if email_queue is not None:
                bundle.email_queue = email_queue
        else:
            logger.warning(
                "Flushing bundle %s with channel '%s' — no dispatch "
                "infrastructure; marking SENT without delivery.",
                bundle.pk,
                bundle.channel,
            )

        before = NotificationEngineService._serialize_bundle(bundle)
        bundle.status = NotificationBundle.Status.SENT
        bundle.dispatched_at = timezone.now()
        bundle.save()
        after = NotificationEngineService._serialize_bundle(bundle)
        NotificationEngineService._log_audit(
            society=bundle.society,
            action=GateOpsAuditLog.Action.STATE_TRANSITION,
            bundle=bundle,
            before=before,
            after=after,
            actor=actor,
        )
        return bundle

    @staticmethod
    @transaction.atomic
    def flush_pending_bundles(*, society, actor=None) -> int:
        """Flush all PENDING bundles whose bundling window has expired.

        Finds all PENDING bundles for the society where
        ``created_at < now - bundle_window_minutes`` and dispatches each one
        via :meth:`flush_bundle`.

        Returns the count of flushed bundles.
        """
        now = timezone.now()
        pending = list(
            NotificationBundle.objects.filter(
                society=society,
                status=NotificationBundle.Status.PENDING,
                is_active=True,
            ).order_by("created_at")
        )

        flushed = 0
        for bundle in pending:
            # A bundle is ready to flush when its window has elapsed. A
            # window of 0 means "dispatch immediately" — those should have
            # been dispatched in ``dispatch_for_event``, but if they remain
            # PENDING (e.g. non-email channel), flush them now.
            window = bundle.bundle_window_minutes or 0
            if window == 0 or bundle.created_at < now - timedelta(
                minutes=window
            ):
                NotificationEngineService.flush_bundle(
                    bundle=bundle, actor=actor
                )
                flushed += 1
        return flushed

    # ------------------------------------------------------------------ #
    # 6. Repeat Suppression
    # ------------------------------------------------------------------ #

    @staticmethod
    def _is_duplicate_notification(
        *, event, trigger, window_minutes: int = DEFAULT_DUPLICATE_WINDOW_MINUTES
    ) -> bool:
        """Check if a notification was already sent for this person + trigger.

        Queries :class:`NotificationBundle` for a PENDING or SENT bundle
        linked to an event with the same person, within the suppression
        window. Returns ``True`` if a duplicate exists (should suppress),
        ``False`` otherwise.

        When ``event.person`` is ``None``, the check is skipped (returns
        ``False``) — without a person anchor, deduplication is impossible.
        """
        if event.person is None:
            return False
        window_start = timezone.now() - timedelta(minutes=window_minutes)
        return NotificationBundle.objects.filter(
            society=event.society,
            visitor_category=event.visitor_category,
            gate_events__person=event.person,
            trigger=trigger,
            status__in=[
                NotificationBundle.Status.PENDING,
                NotificationBundle.Status.SENT,
            ],
            created_at__gte=window_start,
        ).exists()

    # ------------------------------------------------------------------ #
    # 7. Rule Action Dispatch
    # ------------------------------------------------------------------ #

    @staticmethod
    def dispatch_for_rule_action(
        *, event, action, parameters=None, actor=None
    ) -> NotificationBundle | None:
        """Dispatch a notification when a rule evaluation results in a
        notification-related action.

        ``action`` is the :class:`RuleAction.ActionType` string
        (``send_notification``, ``notify_security``, ``escalate``).
        ``parameters`` is the :class:`RuleAction.parameters` dict.

        - **SEND_NOTIFICATION:** uses ``parameters["notify_channels"]`` if
          present (dispatching via each channel), otherwise falls back to
          the default preference. Uses ``parameters["template"]`` if
          present, otherwise the default trigger-based template.
        - **NOTIFY_SECURITY:** creates a PENDING bundle with channel=SMS
          (security guards/admins would be notified in a full
          implementation; for now, the bundle is logged).
        - **ESCALATE:** creates a PENDING bundle and logs the escalation
          (a full implementation would notify supervisors).

        Wrapped in try/except — returns ``None`` on failure so rule-action
        dispatch never blocks gate operations.
        """
        try:
            parameters = parameters or {}
            society = event.society
            trigger = NotificationEngineService._infer_trigger_from_event(
                event=event
            )

            if action == RuleAction.ActionType.SEND_NOTIFICATION:
                return NotificationEngineService._dispatch_send_notification(
                    event=event,
                    parameters=parameters,
                    trigger=trigger,
                    actor=actor,
                )

            if action == RuleAction.ActionType.NOTIFY_SECURITY:
                return NotificationEngineService._dispatch_notify_security(
                    event=event,
                    parameters=parameters,
                    trigger=trigger,
                    actor=actor,
                )

            if action == RuleAction.ActionType.ESCALATE:
                return NotificationEngineService._dispatch_escalate(
                    event=event,
                    parameters=parameters,
                    trigger=trigger,
                    actor=actor,
                )

            # Unknown action — log and return None.
            logger.warning(
                "Unknown rule action '%s' for notification dispatch "
                "(event=%s).",
                action,
                getattr(event, "pk", None),
            )
            return None

        except Exception:  # noqa: BLE001 — never block gate operations.
            logger.exception(
                "Rule-action notification dispatch failed for event %s "
                "(action=%s).",
                getattr(event, "pk", None),
                action,
            )
            return None

    @staticmethod
    def _dispatch_send_notification(
        *, event, parameters, trigger, actor
    ) -> NotificationBundle | None:
        """Handle the SEND_NOTIFICATION rule action."""
        society = event.society
        visitor_category = event.visitor_category
        template_override = parameters.get("template")
        notify_channels = parameters.get("notify_channels") or []

        # If specific channels are requested, dispatch via each. Only EMAIL
        # has infrastructure; others are logged as PENDING bundles.
        if notify_channels:
            host = NotificationEngineService.resolve_host(event=event)
            last_bundle = None
            for channel in notify_channels:
                bundle = NotificationEngineService._create_bundle_for_channel(
                    event=event,
                    channel=channel,
                    trigger=trigger,
                    host=host,
                    actor=actor,
                    template_name=template_override,
                )
                if bundle is not None:
                    last_bundle = bundle
            return last_bundle

        # No explicit channels — fall back to the default preference.
        preference = NotificationEngineService.get_preference_for_trigger(
            society=society,
            visitor_category=visitor_category,
            trigger=trigger,
        )
        if preference is None:
            return None

        host = NotificationEngineService.resolve_host(event=event)
        if host is None:
            bundle = NotificationEngineService._create_skipped_bundle(
                event=event,
                preference=preference,
                trigger=trigger,
                reason="no_host",
            )
            NotificationEngineService._log_audit(
                society=society,
                action=GateOpsAuditLog.Action.STATE_TRANSITION,
                bundle=bundle,
                before=None,
                after={"status": "skipped", "reason": "no_host"},
                actor=actor,
            )
            return bundle

        bundle = NotificationEngineService._find_or_create_bundle(
            event=event,
            preference=preference,
            trigger=trigger,
            host=host,
        )

        if (
            preference.channel == NotificationPreference.Channel.EMAIL
            and preference.bundle_window_minutes == 0
        ):
            email_queue = NotificationEngineService._dispatch_email(
                event=event,
                host=host,
                preference=preference,
                trigger=trigger,
                bundle=bundle,
                template_name=template_override,
            )
            if email_queue is not None:
                bundle.email_queue = email_queue
            bundle.status = NotificationBundle.Status.SENT
            bundle.dispatched_at = timezone.now()
            bundle.save()
        elif preference.channel != NotificationPreference.Channel.EMAIL:
            logger.warning(
                "SEND_NOTIFICATION via channel '%s' — no dispatch "
                "infrastructure; bundle %s left PENDING (event=%s).",
                preference.channel,
                bundle.pk,
                event.pk,
            )

        NotificationEngineService._log_audit(
            society=society,
            action=GateOpsAuditLog.Action.CREATE,
            bundle=bundle,
            before=None,
            after=NotificationEngineService._serialize_bundle(bundle),
            actor=actor,
        )
        return bundle

    @staticmethod
    def _dispatch_notify_security(
        *, event, parameters, trigger, actor
    ) -> NotificationBundle | None:
        """Handle the NOTIFY_SECURITY rule action.

        Creates a PENDING bundle with channel=SMS. A full implementation
        would resolve security guards/admins via :class:`GateOpsRole` and
        dispatch SMS messages; for now, the bundle is created and the
        escalation is logged.
        """
        logger.warning(
            "NOTIFY_SECURITY for event %s — security notification bundle "
            "created (SMS channel, PENDING). Full guard resolution not yet "
            "implemented.",
            getattr(event, "pk", None),
        )
        bundle = NotificationBundle(
            society=event.society,
            visitor_category=event.visitor_category,
            host_unit=event.host_unit,
            trigger=trigger,
            channel=NotificationPreference.Channel.SMS,
            recipient_email="",
            bundle_window_minutes=0,
            status=NotificationBundle.Status.PENDING,
        )
        bundle.save()
        bundle.gate_events.add(event)
        NotificationEngineService._log_audit(
            society=event.society,
            action=GateOpsAuditLog.Action.ESCALATE,
            bundle=bundle,
            before=None,
            after=NotificationEngineService._serialize_bundle(bundle),
            actor=actor,
        )
        return bundle

    @staticmethod
    def _dispatch_escalate(
        *, event, parameters, trigger, actor
    ) -> NotificationBundle | None:
        """Handle the ESCALATE rule action.

        Logs the escalation and creates a PENDING bundle. A full
        implementation would notify supervisors via push/SMS/email.
        """
        escalate_to = parameters.get("escalate_to", "supervisor")
        logger.warning(
            "ESCALATE for event %s — escalating to '%s'. Supervisor "
            "notification not yet implemented; bundle created as PENDING.",
            getattr(event, "pk", None),
            escalate_to,
        )
        bundle = NotificationBundle(
            society=event.society,
            visitor_category=event.visitor_category,
            host_unit=event.host_unit,
            trigger=trigger,
            channel=NotificationPreference.Channel.PUSH,
            recipient_email="",
            bundle_window_minutes=0,
            status=NotificationBundle.Status.PENDING,
        )
        bundle.save()
        bundle.gate_events.add(event)
        NotificationEngineService._log_audit(
            society=event.society,
            action=GateOpsAuditLog.Action.ESCALATE,
            bundle=bundle,
            before=None,
            after=NotificationEngineService._serialize_bundle(bundle),
            actor=actor,
        )
        return bundle

    # ------------------------------------------------------------------ #
    # 8. Audit Logging
    # ------------------------------------------------------------------ #

    @staticmethod
    def _log_audit(
        *,
        society,
        action,
        bundle=None,
        before=None,
        after=None,
        actor=None,
    ) -> None:
        """Write an append-only GateOpsAuditLog entry for a notification op.

        Wrapped so a logging failure never blocks a legitimate notification
        operation; the error is logged at ERROR level instead.

        ``bundle`` may be ``None`` for aggregate actions that are not tied
        to a single bundle row; in that case ``entity_id`` is recorded as
        an empty string.
        """
        try:
            GateOpsAuditLog.log(
                society=society,
                action=action,
                entity_type="NotificationBundle",
                entity_id=str(bundle.pk) if bundle is not None else "",
                actor=actor,
                before_value=before,
                after_value=after,
            )
        except Exception:  # noqa: BLE001 — audit must not break the operation.
            logger.exception(
                "Failed to write NotificationBundle audit log for bundle %s "
                "(action=%s).",
                getattr(bundle, "pk", None),
                action,
            )

    # ------------------------------------------------------------------ #
    # 9. Query Methods
    # ------------------------------------------------------------------ #

    @staticmethod
    def list_bundles(
        *, society, status=None, include_inactive=False
    ) -> QuerySet:
        """Return notification bundles for the society.

        Filter by ``status`` if provided. Active-only by default
        (``include_inactive=False``). Uses ``select_related`` on
        ``society``, ``visitor_category``, ``host_unit``, and
        ``email_queue`` to avoid N+1 on display.
        """
        qs = NotificationBundle.objects.filter(society=society).select_related(
            "society",
            "visitor_category",
            "host_unit",
            "email_queue",
        )
        if status is not None:
            qs = qs.filter(status=status)
        if not include_inactive:
            qs = qs.filter(is_active=True)
        return qs.order_by("-created_at")

    @staticmethod
    def get_bundle(*, society, pk) -> NotificationBundle:
        """Return a single active bundle or raise Http404.

        Scoped by ``society`` + ``is_active=True`` so a soft-deleted or
        cross-tenant bundle is never returned.
        """
        return get_object_or_404(
            NotificationBundle, society=society, pk=pk, is_active=True
        )

    @staticmethod
    def get_pending_bundle_count(*, society) -> int:
        """Return the count of PENDING bundles for the society."""
        return NotificationBundle.objects.filter(
            society=society,
            status=NotificationBundle.Status.PENDING,
            is_active=True,
        ).count()

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _create_skipped_bundle(
        *, event, preference, trigger, reason: str
    ) -> NotificationBundle:
        """Create a SKIPPED bundle for traceability (silent / no-host).

        The event is linked to the bundle's ``gate_events`` M2M so the
        suppression is auditable.
        """
        bundle = NotificationBundle(
            society=event.society,
            visitor_category=event.visitor_category,
            host_unit=event.host_unit,
            trigger=trigger,
            channel=preference.channel,
            recipient_email="",
            bundle_window_minutes=preference.bundle_window_minutes,
            status=NotificationBundle.Status.SKIPPED,
        )
        bundle.save()
        bundle.gate_events.add(event)
        return bundle

    @staticmethod
    @transaction.atomic
    def _create_bundle_for_channel(
        *,
        event,
        channel,
        trigger,
        host,
        actor=None,
        template_name=None,
    ) -> NotificationBundle | None:
        """Create and dispatch a bundle for a specific channel.

        Used by :meth:`_dispatch_send_notification` when explicit channels
        are requested via rule-action parameters.
        """
        recipient_email = host.get("email", "") if host else ""
        bundle = NotificationBundle(
            society=event.society,
            visitor_category=event.visitor_category,
            host_unit=event.host_unit,
            trigger=trigger,
            channel=channel,
            recipient_email=recipient_email,
            bundle_window_minutes=0,
            status=NotificationBundle.Status.PENDING,
        )
        bundle.save()
        bundle.gate_events.add(event)

        if channel == NotificationPreference.Channel.EMAIL:
            if host and recipient_email:
                email_queue = NotificationEngineService._dispatch_email(
                    event=event,
                    host=host,
                    preference=None,
                    trigger=trigger,
                    bundle=bundle,
                    template_name=template_name,
                )
                if email_queue is not None:
                    bundle.email_queue = email_queue
                bundle.status = NotificationBundle.Status.SENT
                bundle.dispatched_at = timezone.now()
                bundle.save()
            else:
                bundle.status = NotificationBundle.Status.SKIPPED
                bundle.save()
        else:
            logger.warning(
                "Rule-action dispatch via channel '%s' — no dispatch "
                "infrastructure; bundle %s left PENDING (event=%s).",
                channel,
                bundle.pk,
                event.pk,
            )

        NotificationEngineService._log_audit(
            society=event.society,
            action=GateOpsAuditLog.Action.CREATE,
            bundle=bundle,
            before=None,
            after=NotificationEngineService._serialize_bundle(bundle),
            actor=actor,
        )
        return bundle

    @staticmethod
    def _build_email_context(*, event, host) -> dict:
        """Build the email template context from the event and host."""
        person = event.person
        return {
            "visitor_name": person.name if person else "Unknown",
            "visitor_phone": person.phone if person else "",
            "visitor_category": (
                event.visitor_category.name if event.visitor_category else ""
            ),
            "purpose": event.purpose or "",
            "gate_name": event.gate.name if event.gate else "",
            "host_name": host.get("name", "") if host else "",
            "society_name": event.society.name if event.society else "",
            "entered_at": (
                event.entered_at.isoformat() if event.entered_at else ""
            ),
            "exited_at": (
                event.exited_at.isoformat() if event.exited_at else ""
            ),
            "auto_closed_at": (
                event.auto_close_at.isoformat()
                if event.auto_close_at
                else ""
            ),
            # Parcel-template fields (populated as empty for visitor
            # notifications; the parcel service overrides these when
            # dispatching parcel-ready emails).
            "tracking_number": "",
            "courier": "",
            "received_at": "",
        }

    @staticmethod
    def _select_template(
        *, event, trigger, template_name: str | None = None
    ) -> str:
        """Select the email template name based on the trigger.

        - ``Trigger.ARRIVAL`` → ``gateops.visitor_arrival`` (or
          ``gateops.approval_request`` when the event requires approval).
        - ``Trigger.ENTRY`` → ``gateops.visitor_entry``.
        - ``Trigger.EXIT`` → ``gateops.visitor_exit``.

        When ``template_name`` is provided (rule-action override), it is
        returned unchanged.
        """
        if template_name:
            return template_name

        if trigger == NotificationPreference.Trigger.ARRIVAL:
            # Use the approval-request template when the event is pending
            # approval (status=ARRIVED) or the visitor category requires
            # approval by default.
            requires_approval = (
                event.status == GateEvent.Status.ARRIVED
                or getattr(
                    event.visitor_category, "requires_approval_default", False
                )
            )
            return (
                "gateops.approval_request"
                if requires_approval
                else "gateops.visitor_arrival"
            )

        if trigger == NotificationPreference.Trigger.ENTRY:
            return "gateops.visitor_entry"

        if trigger == NotificationPreference.Trigger.EXIT:
            # Auto-closed events use a dedicated template so the host is
            # informed the visitor left (or was auto-closed) without an
            # explicit exit scan.
            if (
                event.status == GateEvent.Status.AUTO_CLOSED
                or event.event_type == GateEvent.EventType.AUTO_CLOSE
            ):
                return "gateops.auto_close"
            return "gateops.visitor_exit"

        # Fallback for any other trigger value.
        return "gateops.visitor_arrival"

    @staticmethod
    def _infer_trigger_from_event(*, event) -> str:
        """Infer the notification trigger from the event's current status.

        - ARRIVED / APPROVED → ARRIVAL
        - ENTERED → ENTRY
        - EXITED / AUTO_CLOSED → EXIT
        - default → ARRIVAL
        """
        status_map = {
            GateEvent.Status.ARRIVED: NotificationPreference.Trigger.ARRIVAL,
            GateEvent.Status.APPROVED: NotificationPreference.Trigger.ARRIVAL,
            GateEvent.Status.ENTERED: NotificationPreference.Trigger.ENTRY,
            GateEvent.Status.EXITED: NotificationPreference.Trigger.EXIT,
            GateEvent.Status.AUTO_CLOSED: NotificationPreference.Trigger.EXIT,
        }
        return status_map.get(
            event.status, NotificationPreference.Trigger.ARRIVAL
        )

    @staticmethod
    def _serialize_bundle(bundle) -> dict:
        """Return a JSON-safe dict of the bundle's key fields for audit."""
        def _dt(value):
            return value.isoformat() if value else None

        return {
            "id": str(bundle.pk),
            "status": bundle.status,
            "channel": bundle.channel,
            "trigger": bundle.trigger,
            "recipient_email": bundle.recipient_email,
            "bundle_window_minutes": bundle.bundle_window_minutes,
            "host_unit_id": (
                str(bundle.host_unit_id) if bundle.host_unit_id else None
            ),
            "visitor_category_id": (
                str(bundle.visitor_category_id)
                if bundle.visitor_category_id
                else None
            ),
            "email_queue_id": (
                str(bundle.email_queue_id) if bundle.email_queue_id else None
            ),
            "dispatched_at": _dt(bundle.dispatched_at),
            "created_at": _dt(bundle.created_at),
        }
