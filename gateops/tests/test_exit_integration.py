"""
Test suite for gateops Phase 12 — Exit Management integration tests.

End-to-end integration tests covering:
- Exit triggers notification (EXIT trigger dispatched via notification engine).
- Exit creates audit log (GateOpsAuditLog.Action.EXIT).
- Handover snapshot survives auto-close (ShiftHandoverItem remains after the
  underlying GateEvent is auto-closed).
- Cross-gate exit allowed (visitor enters at gate A, exits at gate B).
- Non-blocking: audit log failure doesn't block exit; notification failure
  doesn't block exit.
- Full handover lifecycle: create → acknowledge, create → dispute →
  acknowledge.
- Handover snapshot captures correct denormalized fields.
- Exit transitions event from ENTERED to EXITED with correct timestamps.

Test conventions follow test_ai_integration.py:
- SocietyTestCase base class provides cls.society and cls.user.
- cls.other_society created for cross-society validation tests.
- Seeded Gate and VisitorCategory fetched in setUpTestData().
- _make_* helpers create per-test mutable records.
- _make_entered_event drives an event through invited→arrived→approved→entered.
- @patch("gateops.services.notification_engine.queue_email") mocks notifications.
"""
import uuid
from datetime import time, timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.utils import timezone

from core.test_base import SocietyTestCase
from core.test_factories import SocietyFactory
from gateops.models import (
    Gate,
    GateEvent,
    GateOpsAuditLog,
    GuardShift,
    MaterialCategory,
    MaterialMovement,
    NotificationPreference,
    Parcel,
    Person,
    SecurityGuard,
    ShiftHandover,
    ShiftHandoverItem,
    VisitorCategory,
)
from gateops.services.exit_management_service import ExitManagementService
from gateops.services.gate_event_lifecycle import GateEventLifecycleService
from gateops.services.shift_handover_service import ShiftHandoverService


class ExitIntegrationTestBase(SocietyTestCase):
    """Shared fixtures for Phase 12 exit management integration tests."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.other_society = SocietyFactory(name="Test Society Beta")
        cls.gate = Gate.objects.get(society=cls.society, code="MAIN")
        cls.other_gate = Gate.objects.get(society=cls.other_society, code="MAIN")
        cls.visitor_cat = VisitorCategory.objects.get(
            society=cls.society, code="GUEST"
        )
        cls.shift = GuardShift.objects.create(
            society=cls.society,
            name="Morning",
            start_time=time(6, 0),
            end_time=time(14, 0),
        )

    def setUp(self):
        super().setUp()
        cache.clear()

    # --- helpers ---------------------------------------------------------

    def _make_guard(self, label="Guard", society=None):
        return SecurityGuard.objects.create(
            society=society or self.society,
            name=f"{label} {uuid.uuid4().hex[:6]}",
            phone=f"{uuid.uuid4().int % (10**10):010d}",
            badge_number=f"B{uuid.uuid4().hex[:6]}",
        )

    def _make_person(self, society=None, name=None):
        return Person.objects.create(
            society=society or self.society,
            name=name or f"Visitor {uuid.uuid4().hex[:6]}",
            phone=f"{uuid.uuid4().int % (10**10):010d}",
        )

    def _make_entered_event(self, person=None, gate=None, society=None):
        soc = society or self.society
        g = gate or self.gate
        vc = (
            self.visitor_cat
            if soc == self.society
            else VisitorCategory.objects.get(society=soc, code="GUEST")
        )
        guard = self._make_guard(society=soc)
        event = GateEventLifecycleService.create_invitation(
            society=soc,
            visitor_category=vc,
            person=person or self._make_person(society=soc),
            expected_arrival_at=timezone.now(),
            created_by=self.user,
            gate=g,
        )
        GateEventLifecycleService.record_arrival(event, gate=g, guard=guard)
        event.refresh_from_db()
        GateEventLifecycleService.approve(event, approved_by=self.user)
        event.refresh_from_db()
        GateEventLifecycleService.record_entry(event, guard=guard)
        event.refresh_from_db()
        return event


# ======================================================================== #
# Exit transition integration
# ======================================================================== #
class ExitTransitionIntegrationTest(ExitIntegrationTestBase):
    """Tests for the exit transition end-to-end behavior."""

    @patch("gateops.services.notification_engine.queue_email")
    def test_exit_transitions_entered_to_exited(self, mock_queue):
        event = self._make_entered_event()
        result = ExitManagementService.process_quick_exit(
            society=self.society, gate_event_id=str(event.event_uuid)
        )
        result.refresh_from_db()
        self.assertEqual(result.status, GateEvent.Status.EXITED)
        self.assertIsNotNone(result.exited_at)
        self.assertGreater(result.exited_at, result.entered_at)

    @patch("gateops.services.notification_engine.queue_email")
    def test_exit_creates_audit_log(self, mock_queue):
        event = self._make_entered_event()
        ExitManagementService.process_quick_exit(
            society=self.society, gate_event_id=str(event.event_uuid)
        )
        audit = GateOpsAuditLog.objects.filter(
            society=self.society,
            action=GateOpsAuditLog.Action.EXIT,
            entity_type="GateEvent",
            entity_id=str(event.pk),
        )
        self.assertTrue(audit.exists())

    @patch("gateops.services.notification_engine.queue_email")
    def test_exit_dispatches_notification(self, mock_queue):
        """record_exit calls _notify with the EXIT trigger."""
        event = self._make_entered_event()
        with patch.object(
            GateEventLifecycleService, "_notify"
        ) as mock_notify:
            ExitManagementService.process_quick_exit(
                society=self.society, gate_event_id=str(event.event_uuid)
            )
            mock_notify.assert_called_once()
            call_kwargs = mock_notify.call_args
            self.assertEqual(
                call_kwargs.args[1], NotificationPreference.Trigger.EXIT
            )

    @patch("gateops.services.notification_engine.queue_email")
    def test_exit_does_not_create_duplicate_audit(self, mock_queue):
        """The exit service delegates to record_exit which writes the audit;
        the service itself should NOT add a second audit entry."""
        event = self._make_entered_event()
        ExitManagementService.process_quick_exit(
            society=self.society, gate_event_id=str(event.event_uuid)
        )
        audit_count = GateOpsAuditLog.objects.filter(
            society=self.society,
            action=GateOpsAuditLog.Action.EXIT,
            entity_type="GateEvent",
            entity_id=str(event.pk),
        ).count()
        self.assertEqual(audit_count, 1)

    @patch("gateops.services.notification_engine.queue_email")
    def test_cross_gate_exit_allowed(self, mock_queue):
        """A visitor may exit at a gate different from their entry gate."""
        second_gate = Gate.objects.create(
            society=self.society,
            name="Back Gate",
            code="BACK",
            gate_type=Gate.GateType.SERVICE,
        )
        event = self._make_entered_event(gate=self.gate)
        result = ExitManagementService.process_quick_exit(
            society=self.society,
            gate_event_id=str(event.event_uuid),
            gate=second_gate,
        )
        result.refresh_from_db()
        self.assertEqual(result.status, GateEvent.Status.EXITED)
        # The entry gate is preserved.
        self.assertEqual(result.gate, self.gate)

    @patch("gateops.services.notification_engine.queue_email")
    def test_exit_assigns_exit_guard(self, mock_queue):
        event = self._make_entered_event()
        exit_guard = self._make_guard("Exit")
        result = ExitManagementService.process_quick_exit(
            society=self.society,
            gate_event_id=str(event.event_uuid),
            guard=exit_guard,
        )
        result.refresh_from_db()
        self.assertEqual(result.guard, exit_guard)


# ======================================================================== #
# Non-blocking behavior
# ======================================================================== #
class NonBlockingIntegrationTest(ExitIntegrationTestBase):
    """Tests that audit and notification failures don't block exits."""

    @patch("gateops.services.notification_engine.queue_email")
    def test_audit_failure_does_not_block_exit(self, mock_queue):
        event = self._make_entered_event()
        with patch.object(
            GateOpsAuditLog,
            "log",
            side_effect=Exception("Audit DB down"),
        ):
            # record_exit's _log_audit is wrapped in try/except, so the exit
            # should still succeed.
            result = ExitManagementService.process_quick_exit(
                society=self.society, gate_event_id=str(event.event_uuid)
            )
        result.refresh_from_db()
        self.assertEqual(result.status, GateEvent.Status.EXITED)

    @patch("gateops.services.notification_engine.queue_email")
    def test_notification_failure_does_not_block_exit(self, mock_queue):
        event = self._make_entered_event()
        # Patch the inner dispatch_for_event so _notify's own try/except
        # catches the exception (patching _notify itself would bypass its
        # internal error handling).
        with patch(
            "gateops.services.gate_event_lifecycle.NotificationEngineService.dispatch_for_event",
            side_effect=Exception("Notification service down"),
        ):
            result = ExitManagementService.process_quick_exit(
                society=self.society, gate_event_id=str(event.event_uuid)
            )
        result.refresh_from_db()
        self.assertEqual(result.status, GateEvent.Status.EXITED)

    @patch("gateops.services.notification_engine.queue_email")
    def test_handover_create_audit_failure_doesnt_block(self, mock_queue):
        """A failure in audit logging should not block handover creation."""
        outgoing = self._make_guard("Outgoing")
        incoming = self._make_guard("Incoming")
        with patch.object(
            GateOpsAuditLog,
            "log",
            side_effect=Exception("Audit DB down"),
        ):
            handover = ShiftHandoverService.create_shift_handover(
                society=self.society,
                outgoing_guard=outgoing,
                incoming_guard=incoming,
                gate=self.gate,
            )
        self.assertIsNotNone(handover.pk)
        self.assertEqual(handover.status, ShiftHandover.Status.PENDING)

    @patch("gateops.services.notification_engine.queue_email")
    def test_handover_create_notification_failure_doesnt_block(self, mock_queue):
        """A failure in notification should not block handover creation.

        Patch the inner EmailQueue.objects.create so _notify_incoming_guard's
        own try/except catches the exception (patching _notify_incoming_guard
        itself would bypass its internal error handling).
        """
        outgoing = self._make_guard("Outgoing")
        incoming = self._make_guard("Incoming")
        with patch(
            "notifications.models.EmailQueue.objects.create",
            side_effect=Exception("Email service down"),
        ):
            handover = ShiftHandoverService.create_shift_handover(
                society=self.society,
                outgoing_guard=outgoing,
                incoming_guard=incoming,
                gate=self.gate,
            )
        self.assertIsNotNone(handover.pk)
        self.assertEqual(handover.status, ShiftHandover.Status.PENDING)


# ======================================================================== #
# Handover snapshot survives auto-close
# ======================================================================== #
class HandoverSnapshotIntegrationTest(ExitIntegrationTestBase):
    """Tests that ShiftHandoverItem snapshots survive later transitions."""

    @patch("gateops.services.notification_engine.queue_email")
    def test_snapshot_survives_auto_close(self, mock_queue):
        """After a handover is created with inside events, auto-closing one
        of those events should NOT delete or alter the ShiftHandoverItem."""
        event = self._make_entered_event(gate=self.gate)
        outgoing = self._make_guard("Outgoing")
        incoming = self._make_guard("Incoming")
        handover = ShiftHandoverService.create_shift_handover(
            society=self.society,
            outgoing_guard=outgoing,
            incoming_guard=incoming,
            gate=self.gate,
        )
        # Verify the snapshot exists.
        self.assertEqual(handover.items.count(), 1)
        item = handover.items.first()
        original_person = item.person
        original_duration = item.duration_minutes_at_handover

        # Auto-close the event.
        event.refresh_from_db()
        GateEventLifecycleService.auto_close(event)
        event.refresh_from_db()
        self.assertEqual(event.status, GateEvent.Status.AUTO_CLOSED)

        # The snapshot item should be unchanged.
        item.refresh_from_db()
        self.assertEqual(item.person, original_person)
        self.assertEqual(item.duration_minutes_at_handover, original_duration)
        self.assertEqual(handover.items.count(), 1)

    @patch("gateops.services.notification_engine.queue_email")
    def test_snapshot_survives_exit(self, mock_queue):
        """After a handover is created, exiting one of the inside events
        should NOT delete or alter the ShiftHandoverItem."""
        event = self._make_entered_event(gate=self.gate)
        outgoing = self._make_guard("Outgoing")
        incoming = self._make_guard("Incoming")
        handover = ShiftHandoverService.create_shift_handover(
            society=self.society,
            outgoing_guard=outgoing,
            incoming_guard=incoming,
            gate=self.gate,
        )
        self.assertEqual(handover.items.count(), 1)
        item = handover.items.first()
        original_entered_at = item.entered_at

        # Exit the event.
        event.refresh_from_db()
        GateEventLifecycleService.record_exit(event)
        event.refresh_from_db()
        self.assertEqual(event.status, GateEvent.Status.EXITED)

        # The snapshot item should be unchanged.
        item.refresh_from_db()
        self.assertEqual(item.entered_at, original_entered_at)
        self.assertEqual(handover.items.count(), 1)

    @patch("gateops.services.notification_engine.queue_email")
    def test_snapshot_captures_correct_fields(self, mock_queue):
        """The snapshot item captures denormalized fields from the event."""
        person = self._make_person(name="Snapshot Test Person")
        event = self._make_entered_event(person=person, gate=self.gate)
        outgoing = self._make_guard("Outgoing")
        incoming = self._make_guard("Incoming")
        handover = ShiftHandoverService.create_shift_handover(
            society=self.society,
            outgoing_guard=outgoing,
            incoming_guard=incoming,
            gate=self.gate,
        )
        item = handover.items.first()
        self.assertEqual(item.person, person)
        self.assertEqual(item.gate_event, event)
        self.assertEqual(item.gate, self.gate)
        self.assertEqual(item.visitor_category, self.visitor_cat)
        self.assertIsNotNone(item.entered_at)
        self.assertIsInstance(item.duration_minutes_at_handover, int)
        self.assertFalse(item.is_overstay)

    @patch("gateops.services.notification_engine.queue_email")
    def test_snapshot_marks_overstay(self, mock_queue):
        """If the event is overstay at handover time, the item is flagged."""
        event = self._make_entered_event(gate=self.gate)
        # Mark as overstay: auto_close_at in the past.
        # Use QuerySet.update() to bypass clean() which rejects past
        # auto_close_at for ENTERED events.
        GateEvent.objects.filter(pk=event.pk).update(
            auto_close_at=timezone.now() - timedelta(minutes=5),
        )
        event.refresh_from_db()
        outgoing = self._make_guard("Outgoing")
        incoming = self._make_guard("Incoming")
        handover = ShiftHandoverService.create_shift_handover(
            society=self.society,
            outgoing_guard=outgoing,
            incoming_guard=incoming,
            gate=self.gate,
        )
        item = handover.items.first()
        self.assertTrue(item.is_overstay)


# ======================================================================== #
# Full handover lifecycle
# ======================================================================== #
class FullHandoverLifecycleIntegrationTest(ExitIntegrationTestBase):
    """End-to-end tests for the full handover lifecycle."""

    @patch("gateops.services.notification_engine.queue_email")
    def test_full_lifecycle_create_then_acknowledge(self, mock_queue):
        """Create a handover, then acknowledge it."""
        outgoing = self._make_guard("Outgoing")
        incoming = self._make_guard("Incoming")
        handover = ShiftHandoverService.create_shift_handover(
            society=self.society,
            outgoing_guard=outgoing,
            incoming_guard=incoming,
            gate=self.gate,
        )
        self.assertEqual(handover.status, ShiftHandover.Status.PENDING)

        result = ShiftHandoverService.acknowledge_handover(
            society=self.society,
            handover_id=str(handover.handover_uuid),
            incoming_guard=incoming,
            notes="All items verified",
        )
        self.assertEqual(result.status, ShiftHandover.Status.ACKNOWLEDGED)
        self.assertEqual(result.incoming_notes, "All items verified")
        self.assertIsNotNone(result.acknowledged_at)

    @patch("gateops.services.notification_engine.queue_email")
    def test_full_lifecycle_create_then_dispute_then_acknowledge(self, mock_queue):
        """Create a handover, dispute it, then acknowledge (resolve dispute)."""
        outgoing = self._make_guard("Outgoing")
        incoming = self._make_guard("Incoming")
        handover = ShiftHandoverService.create_shift_handover(
            society=self.society,
            outgoing_guard=outgoing,
            incoming_guard=incoming,
            gate=self.gate,
        )
        self.assertEqual(handover.status, ShiftHandover.Status.PENDING)

        # Dispute.
        result = ShiftHandoverService.dispute_handover(
            society=self.society,
            handover_id=str(handover.handover_uuid),
            incoming_guard=incoming,
            reason="Items count mismatch",
        )
        self.assertEqual(result.status, ShiftHandover.Status.DISPUTED)
        self.assertEqual(result.dispute_reason, "Items count mismatch")

        # Acknowledge (resolve the dispute).
        result = ShiftHandoverService.acknowledge_handover(
            society=self.society,
            handover_id=str(handover.handover_uuid),
            incoming_guard=incoming,
            notes="Dispute resolved",
        )
        self.assertEqual(result.status, ShiftHandover.Status.ACKNOWLEDGED)

    @patch("gateops.services.notification_engine.queue_email")
    def test_full_lifecycle_with_inside_events(self, mock_queue):
        """Create a handover with inside events, acknowledge, verify snapshot."""
        self._make_entered_event(gate=self.gate)
        self._make_entered_event(gate=self.gate)
        outgoing = self._make_guard("Outgoing")
        incoming = self._make_guard("Incoming")
        handover = ShiftHandoverService.create_shift_handover(
            society=self.society,
            outgoing_guard=outgoing,
            incoming_guard=incoming,
            gate=self.gate,
        )
        self.assertEqual(handover.inside_count, 2)
        self.assertEqual(handover.items.count(), 2)

        # Acknowledge.
        result = ShiftHandoverService.acknowledge_handover(
            society=self.society,
            handover_id=str(handover.handover_uuid),
            incoming_guard=incoming,
        )
        self.assertEqual(result.status, ShiftHandover.Status.ACKNOWLEDGED)

        # Snapshot items survive.
        self.assertEqual(handover.items.count(), 2)

    @patch("gateops.services.notification_engine.queue_email")
    def test_full_lifecycle_audit_trail(self, mock_queue):
        """The full lifecycle creates HANDOVER_CREATED and
        HANDOVER_ACKNOWLEDGED audit entries."""
        outgoing = self._make_guard("Outgoing")
        incoming = self._make_guard("Incoming")
        handover = ShiftHandoverService.create_shift_handover(
            society=self.society,
            outgoing_guard=outgoing,
            incoming_guard=incoming,
            gate=self.gate,
        )
        ShiftHandoverService.acknowledge_handover(
            society=self.society,
            handover_id=str(handover.handover_uuid),
            incoming_guard=incoming,
        )
        created_audit = GateOpsAuditLog.objects.filter(
            society=self.society,
            action=GateOpsAuditLog.Action.HANDOVER_CREATED,
            entity_id=str(handover.pk),
        )
        ack_audit = GateOpsAuditLog.objects.filter(
            society=self.society,
            action=GateOpsAuditLog.Action.HANDOVER_ACKNOWLEDGED,
            entity_id=str(handover.pk),
        )
        self.assertTrue(created_audit.exists())
        self.assertTrue(ack_audit.exists())

    @patch("gateops.services.notification_engine.queue_email")
    def test_full_lifecycle_dispute_audit_trail(self, mock_queue):
        """Dispute creates a HANDOVER_DISPUTED audit entry."""
        outgoing = self._make_guard("Outgoing")
        incoming = self._make_guard("Incoming")
        handover = ShiftHandoverService.create_shift_handover(
            society=self.society,
            outgoing_guard=outgoing,
            incoming_guard=incoming,
            gate=self.gate,
        )
        ShiftHandoverService.dispute_handover(
            society=self.society,
            handover_id=str(handover.handover_uuid),
            incoming_guard=incoming,
            reason="Items mismatch",
        )
        dispute_audit = GateOpsAuditLog.objects.filter(
            society=self.society,
            action=GateOpsAuditLog.Action.HANDOVER_DISPUTED,
            entity_id=str(handover.pk),
        )
        self.assertTrue(dispute_audit.exists())


# ======================================================================== #
# Cross-society isolation
# ======================================================================== #
class CrossSocietyIntegrationTest(ExitIntegrationTestBase):
    """Tests for multi-tenant isolation in exit management."""

    @patch("gateops.services.notification_engine.queue_email")
    def test_exit_cross_society_rejected(self, mock_queue):
        other_event = self._make_entered_event(society=self.other_society)
        with self.assertRaises(GateEvent.DoesNotExist):
            ExitManagementService.process_quick_exit(
                society=self.society,
                gate_event_id=str(other_event.event_uuid),
            )

    @patch("gateops.services.notification_engine.queue_email")
    def test_currently_inside_cross_society_isolation(self, mock_queue):
        self._make_entered_event()
        self._make_entered_event(society=self.other_society)
        result = ExitManagementService.get_currently_inside(
            society=self.society
        )
        self.assertEqual(result["total"], 1)

    @patch("gateops.services.notification_engine.queue_email")
    def test_handover_cross_society_rejected(self, mock_queue):
        other_guard = self._make_guard("Other", society=self.other_society)
        with self.assertRaises(ValidationError):
            ShiftHandoverService.create_shift_handover(
                society=self.society,
                outgoing_guard=other_guard,
                incoming_guard=self._make_guard("Incoming"),
                gate=self.gate,
            )

    @patch("gateops.services.notification_engine.queue_email")
    def test_handover_list_cross_society_isolation(self, mock_queue):
        outgoing = self._make_guard("Outgoing")
        incoming = self._make_guard("Incoming")
        ShiftHandoverService.create_shift_handover(
            society=self.society,
            outgoing_guard=outgoing,
            incoming_guard=incoming,
            gate=self.gate,
        )
        # Other society handover.
        other_outgoing = self._make_guard("OO", society=self.other_society)
        other_incoming = self._make_guard("OI", society=self.other_society)
        ShiftHandoverService.create_shift_handover(
            society=self.other_society,
            outgoing_guard=other_outgoing,
            incoming_guard=other_incoming,
            gate=self.other_gate,
        )
        own = ShiftHandoverService.list_handovers(society=self.society)
        other = ShiftHandoverService.list_handovers(society=self.other_society)
        self.assertEqual(own.count(), 1)
        self.assertEqual(other.count(), 1)

    @patch("gateops.services.notification_engine.queue_email")
    def test_inside_count_cross_society_isolation(self, mock_queue):
        self._make_entered_event()
        self._make_entered_event(society=self.other_society)
        own_count = ExitManagementService.get_currently_inside_count(
            society=self.society
        )
        other_count = ExitManagementService.get_currently_inside_count(
            society=self.other_society
        )
        self.assertEqual(own_count, 1)
        self.assertEqual(other_count, 1)
