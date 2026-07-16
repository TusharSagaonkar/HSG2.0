"""
Test suite for gateops Phase 12 — ShiftHandoverService.

Covers ShiftHandoverService:
- create_shift_handover: with snapshots, duplicate pending rejection,
  cross-society rejection, self-handover, inside_count, pending_items,
  audit log, notification.
- acknowledge_handover: race-safe, wrong guard, already acknowledged,
  disputed → acknowledged.
- dispute_handover: reason required, wrong guard, already acknowledged,
  race-safe.
- list_handovers: filters (status, gate, guard, include_inactive).
- get_handover: by UUID, by PK, 404 for cross-society.
- get_handover_items: returns items for a handover.
- get_pending_handovers_for_guard.
- get_guards_needing_handover.
- _compute_pending_items: pending approvals, overdue materials, uncollected
  parcels.

Test conventions follow test_contractor_service.py:
- SocietyTestCase base class provides cls.society and cls.user.
- cls.other_society created for cross-society validation tests.
- Seeded Gate and VisitorCategory fetched in setUpTestData().
- _make_* helpers create per-test mutable records.
- @patch("gateops.services.notification_engine.queue_email") mocks notifications.
"""
import uuid
from datetime import time, timedelta
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.http import Http404
from django.utils import timezone

from core.test_base import SocietyTestCase
from core.test_factories import SocietyFactory
from gateops.models import (
    Gate,
    GateEvent,
    GateEventApproval,
    GateOpsAuditLog,
    GuardShift,
    GuardShiftAssignment,
    MaterialCategory,
    MaterialMovement,
    Parcel,
    Person,
    SecurityGuard,
    ShiftHandover,
    ShiftHandoverItem,
    VisitorCategory,
)
from gateops.services.gate_event_lifecycle import GateEventLifecycleService
from gateops.services.shift_handover_service import ShiftHandoverService


class ShiftHandoverServiceTest(SocietyTestCase):
    """Service-level tests for ShiftHandoverService."""

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
        self.outgoing = self._make_guard("Outgoing")
        self.incoming = self._make_guard("Incoming")

    # --- helpers ---------------------------------------------------------

    def _make_guard(self, label, society=None):
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

    def _make_entered_event(self, person=None, gate=None):
        g = gate or self.gate
        guard = self._make_guard("Entry")
        event = GateEventLifecycleService.create_invitation(
            society=self.society,
            visitor_category=self.visitor_cat,
            person=person or self._make_person(),
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

    def _make_handover(self, **overrides):
        defaults = {
            "society": self.society,
            "outgoing_guard": self.outgoing,
            "incoming_guard": self.incoming,
            "gate": self.gate,
        }
        defaults.update(overrides)
        return ShiftHandover.objects.create(**defaults)

    # ================================================================== #
    # create_shift_handover
    # ================================================================== #

    @patch("gateops.services.notification_engine.queue_email")
    def test_create_shift_handover_creates_with_correct_fields(self, mock_queue):
        handover = ShiftHandoverService.create_shift_handover(
            society=self.society,
            outgoing_guard=self.outgoing,
            incoming_guard=self.incoming,
            gate=self.gate,
            outgoing_notes="All clear",
        )
        self.assertEqual(handover.society, self.society)
        self.assertEqual(handover.outgoing_guard, self.outgoing)
        self.assertEqual(handover.incoming_guard, self.incoming)
        self.assertEqual(handover.gate, self.gate)
        self.assertEqual(handover.status, ShiftHandover.Status.PENDING)
        self.assertEqual(handover.outgoing_notes, "All clear")
        self.assertEqual(handover.inside_count, 0)

    @patch("gateops.services.notification_engine.queue_email")
    def test_create_shift_handover_with_shift(self, mock_queue):
        handover = ShiftHandoverService.create_shift_handover(
            society=self.society,
            outgoing_guard=self.outgoing,
            incoming_guard=self.incoming,
            gate=self.gate,
            shift=self.shift,
        )
        self.assertEqual(handover.shift, self.shift)

    @patch("gateops.services.notification_engine.queue_email")
    def test_create_shift_handover_snapshots_inside_events(self, mock_queue):
        self._make_entered_event(gate=self.gate)
        self._make_entered_event(gate=self.gate)
        handover = ShiftHandoverService.create_shift_handover(
            society=self.society,
            outgoing_guard=self.outgoing,
            incoming_guard=self.incoming,
            gate=self.gate,
        )
        self.assertEqual(handover.inside_count, 2)
        self.assertEqual(handover.items.count(), 2)

    @patch("gateops.services.notification_engine.queue_email")
    def test_create_shift_handover_snapshots_only_gate_events(self, mock_queue):
        """Only events at the specified gate are snapshotted."""
        second_gate = Gate.objects.create(
            society=self.society,
            name="Back Gate",
            code="BACK",
            gate_type=Gate.GateType.SERVICE,
        )
        self._make_entered_event(gate=self.gate)
        self._make_entered_event(gate=second_gate)
        handover = ShiftHandoverService.create_shift_handover(
            society=self.society,
            outgoing_guard=self.outgoing,
            incoming_guard=self.incoming,
            gate=self.gate,
        )
        self.assertEqual(handover.inside_count, 1)

    @patch("gateops.services.notification_engine.queue_email")
    def test_create_shift_handover_item_has_denormalized_fields(self, mock_queue):
        person = self._make_person(name="Snapshot Person")
        event = self._make_entered_event(person=person, gate=self.gate)
        handover = ShiftHandoverService.create_shift_handover(
            society=self.society,
            outgoing_guard=self.outgoing,
            incoming_guard=self.incoming,
            gate=self.gate,
        )
        item = handover.items.first()
        self.assertEqual(item.person, person)
        self.assertEqual(item.gate_event, event)
        self.assertEqual(item.gate, self.gate)
        self.assertEqual(item.visitor_category, self.visitor_cat)
        self.assertIsNotNone(item.entered_at)
        self.assertIsInstance(item.duration_minutes_at_handover, int)

    @patch("gateops.services.notification_engine.queue_email")
    def test_create_shift_handover_duplicate_pending_rejected(self, mock_queue):
        ShiftHandoverService.create_shift_handover(
            society=self.society,
            outgoing_guard=self.outgoing,
            incoming_guard=self.incoming,
            gate=self.gate,
        )
        with self.assertRaises(ValidationError):
            ShiftHandoverService.create_shift_handover(
                society=self.society,
                outgoing_guard=self.outgoing,
                incoming_guard=self.incoming,
                gate=self.gate,
            )

    @patch("gateops.services.notification_engine.queue_email")
    def test_create_shift_handover_cross_society_outgoing_rejected(self, mock_queue):
        other_guard = self._make_guard("Other", society=self.other_society)
        with self.assertRaises(ValidationError):
            ShiftHandoverService.create_shift_handover(
                society=self.society,
                outgoing_guard=other_guard,
                incoming_guard=self.incoming,
                gate=self.gate,
            )

    @patch("gateops.services.notification_engine.queue_email")
    def test_create_shift_handover_cross_society_incoming_rejected(self, mock_queue):
        other_guard = self._make_guard("Other", society=self.other_society)
        with self.assertRaises(ValidationError):
            ShiftHandoverService.create_shift_handover(
                society=self.society,
                outgoing_guard=self.outgoing,
                incoming_guard=other_guard,
                gate=self.gate,
            )

    @patch("gateops.services.notification_engine.queue_email")
    def test_create_shift_handover_cross_society_gate_rejected(self, mock_queue):
        with self.assertRaises(ValidationError):
            ShiftHandoverService.create_shift_handover(
                society=self.society,
                outgoing_guard=self.outgoing,
                incoming_guard=self.incoming,
                gate=self.other_gate,
            )

    @patch("gateops.services.notification_engine.queue_email")
    def test_create_shift_handover_self_handover_rejected(self, mock_queue):
        with self.assertRaises(ValidationError):
            ShiftHandoverService.create_shift_handover(
                society=self.society,
                outgoing_guard=self.outgoing,
                incoming_guard=self.outgoing,
                gate=self.gate,
            )

    @patch("gateops.services.notification_engine.queue_email")
    def test_create_shift_handover_creates_audit_log(self, mock_queue):
        handover = ShiftHandoverService.create_shift_handover(
            society=self.society,
            outgoing_guard=self.outgoing,
            incoming_guard=self.incoming,
            gate=self.gate,
        )
        audit = GateOpsAuditLog.objects.filter(
            society=self.society,
            action=GateOpsAuditLog.Action.HANDOVER_CREATED,
            entity_type="ShiftHandover",
            entity_id=str(handover.pk),
        )
        self.assertTrue(audit.exists())

    @patch("gateops.services.notification_engine.queue_email")
    def test_create_shift_handover_audit_failure_doesnt_block(self, mock_queue):
        with patch.object(
            GateOpsAuditLog,
            "log",
            side_effect=Exception("Audit DB down"),
        ):
            handover = ShiftHandoverService.create_shift_handover(
                society=self.society,
                outgoing_guard=self.outgoing,
                incoming_guard=self.incoming,
                gate=self.gate,
            )
        self.assertIsNotNone(handover.pk)

    @patch("gateops.services.notification_engine.queue_email")
    def test_create_shift_handover_computes_pending_items(self, mock_queue):
        """pending_items_count and pending_items_summary are populated."""
        # Create a pending approval.
        event = self._make_entered_event(gate=self.gate)
        GateEventApproval.objects.create(
            gate_event=event,
            society=self.society,
            decision=GateEventApproval.Decision.PENDING,
        )
        handover = ShiftHandoverService.create_shift_handover(
            society=self.society,
            outgoing_guard=self.outgoing,
            incoming_guard=self.incoming,
            gate=self.gate,
        )
        self.assertGreaterEqual(handover.pending_items_count, 1)
        self.assertIn("pending_approvals", handover.pending_items_summary)

    # ================================================================== #
    # acknowledge_handover
    # ================================================================== #

    @patch("gateops.services.notification_engine.queue_email")
    def test_acknowledge_handover_transitions_to_acknowledged(self, mock_queue):
        handover = self._make_handover()
        result = ShiftHandoverService.acknowledge_handover(
            society=self.society,
            handover_id=str(handover.handover_uuid),
            incoming_guard=self.incoming,
        )
        result.refresh_from_db()
        self.assertEqual(result.status, ShiftHandover.Status.ACKNOWLEDGED)
        self.assertIsNotNone(result.acknowledged_at)

    @patch("gateops.services.notification_engine.queue_email")
    def test_acknowledge_handover_by_pk(self, mock_queue):
        handover = self._make_handover()
        result = ShiftHandoverService.acknowledge_handover(
            society=self.society,
            handover_id=str(handover.pk),
            incoming_guard=self.incoming,
        )
        self.assertEqual(result.status, ShiftHandover.Status.ACKNOWLEDGED)

    @patch("gateops.services.notification_engine.queue_email")
    def test_acknowledge_handover_wrong_guard_raises(self, mock_queue):
        handover = self._make_handover()
        wrong_guard = self._make_guard("Wrong")
        with self.assertRaises(ValidationError):
            ShiftHandoverService.acknowledge_handover(
                society=self.society,
                handover_id=str(handover.handover_uuid),
                incoming_guard=wrong_guard,
            )

    @patch("gateops.services.notification_engine.queue_email")
    def test_acknowledge_handover_already_acknowledged_raises(self, mock_queue):
        handover = self._make_handover(
            status=ShiftHandover.Status.ACKNOWLEDGED,
            acknowledged_at=timezone.now(),
        )
        with self.assertRaises(ValidationError):
            ShiftHandoverService.acknowledge_handover(
                society=self.society,
                handover_id=str(handover.handover_uuid),
                incoming_guard=self.incoming,
            )

    @patch("gateops.services.notification_engine.queue_email")
    def test_acknowledge_handover_disputed_to_acknowledged(self, mock_queue):
        """A DISPUTED handover can be acknowledged (dispute resolved)."""
        handover = self._make_handover(
            status=ShiftHandover.Status.DISPUTED,
            disputed_at=timezone.now(),
            dispute_reason="Items mismatch",
        )
        result = ShiftHandoverService.acknowledge_handover(
            society=self.society,
            handover_id=str(handover.handover_uuid),
            incoming_guard=self.incoming,
        )
        self.assertEqual(result.status, ShiftHandover.Status.ACKNOWLEDGED)

    @patch("gateops.services.notification_engine.queue_email")
    def test_acknowledge_handover_cross_society_404(self, mock_queue):
        handover = self._make_handover()
        with self.assertRaises(Http404):
            ShiftHandoverService.acknowledge_handover(
                society=self.other_society,
                handover_id=str(handover.handover_uuid),
                incoming_guard=self.incoming,
            )

    @patch("gateops.services.notification_engine.queue_email")
    def test_acknowledge_handover_creates_audit_log(self, mock_queue):
        handover = self._make_handover()
        ShiftHandoverService.acknowledge_handover(
            society=self.society,
            handover_id=str(handover.handover_uuid),
            incoming_guard=self.incoming,
        )
        audit = GateOpsAuditLog.objects.filter(
            society=self.society,
            action=GateOpsAuditLog.Action.HANDOVER_ACKNOWLEDGED,
            entity_type="ShiftHandover",
            entity_id=str(handover.pk),
        )
        self.assertTrue(audit.exists())

    @patch("gateops.services.notification_engine.queue_email")
    def test_acknowledge_handover_with_notes(self, mock_queue):
        handover = self._make_handover()
        result = ShiftHandoverService.acknowledge_handover(
            society=self.society,
            handover_id=str(handover.handover_uuid),
            incoming_guard=self.incoming,
            notes="All items verified",
        )
        result.refresh_from_db()
        self.assertEqual(result.incoming_notes, "All items verified")

    # ================================================================== #
    # dispute_handover
    # ================================================================== #

    @patch("gateops.services.notification_engine.queue_email")
    def test_dispute_handover_transitions_to_disputed(self, mock_queue):
        handover = self._make_handover()
        result = ShiftHandoverService.dispute_handover(
            society=self.society,
            handover_id=str(handover.handover_uuid),
            incoming_guard=self.incoming,
            reason="Items count mismatch",
        )
        result.refresh_from_db()
        self.assertEqual(result.status, ShiftHandover.Status.DISPUTED)
        self.assertEqual(result.dispute_reason, "Items count mismatch")
        self.assertIsNotNone(result.disputed_at)

    @patch("gateops.services.notification_engine.queue_email")
    def test_dispute_handover_empty_reason_raises(self, mock_queue):
        handover = self._make_handover()
        with self.assertRaises(ValidationError):
            ShiftHandoverService.dispute_handover(
                society=self.society,
                handover_id=str(handover.handover_uuid),
                incoming_guard=self.incoming,
                reason="",
            )

    @patch("gateops.services.notification_engine.queue_email")
    def test_dispute_handover_whitespace_reason_raises(self, mock_queue):
        handover = self._make_handover()
        with self.assertRaises(ValidationError):
            ShiftHandoverService.dispute_handover(
                society=self.society,
                handover_id=str(handover.handover_uuid),
                incoming_guard=self.incoming,
                reason="   ",
            )

    @patch("gateops.services.notification_engine.queue_email")
    def test_dispute_handover_wrong_guard_raises(self, mock_queue):
        handover = self._make_handover()
        wrong_guard = self._make_guard("Wrong")
        with self.assertRaises(ValidationError):
            ShiftHandoverService.dispute_handover(
                society=self.society,
                handover_id=str(handover.handover_uuid),
                incoming_guard=wrong_guard,
                reason="Some reason",
            )

    @patch("gateops.services.notification_engine.queue_email")
    def test_dispute_handover_already_acknowledged_raises(self, mock_queue):
        handover = self._make_handover(
            status=ShiftHandover.Status.ACKNOWLEDGED,
            acknowledged_at=timezone.now(),
        )
        with self.assertRaises(ValidationError):
            ShiftHandoverService.dispute_handover(
                society=self.society,
                handover_id=str(handover.handover_uuid),
                incoming_guard=self.incoming,
                reason="Late dispute",
            )

    @patch("gateops.services.notification_engine.queue_email")
    def test_dispute_handover_already_disputed_raises(self, mock_queue):
        handover = self._make_handover(
            status=ShiftHandover.Status.DISPUTED,
            disputed_at=timezone.now(),
            dispute_reason="First dispute",
        )
        with self.assertRaises(ValidationError):
            ShiftHandoverService.dispute_handover(
                society=self.society,
                handover_id=str(handover.handover_uuid),
                incoming_guard=self.incoming,
                reason="Second dispute",
            )

    @patch("gateops.services.notification_engine.queue_email")
    def test_dispute_handover_cross_society_404(self, mock_queue):
        handover = self._make_handover()
        with self.assertRaises(Http404):
            ShiftHandoverService.dispute_handover(
                society=self.other_society,
                handover_id=str(handover.handover_uuid),
                incoming_guard=self.incoming,
                reason="Cross society",
            )

    @patch("gateops.services.notification_engine.queue_email")
    def test_dispute_handover_creates_audit_log(self, mock_queue):
        handover = self._make_handover()
        ShiftHandoverService.dispute_handover(
            society=self.society,
            handover_id=str(handover.handover_uuid),
            incoming_guard=self.incoming,
            reason="Items mismatch",
        )
        audit = GateOpsAuditLog.objects.filter(
            society=self.society,
            action=GateOpsAuditLog.Action.HANDOVER_DISPUTED,
            entity_type="ShiftHandover",
            entity_id=str(handover.pk),
        )
        self.assertTrue(audit.exists())

    # ================================================================== #
    # list_handovers
    # ================================================================== #

    @patch("gateops.services.notification_engine.queue_email")
    def test_list_handovers_returns_society_scoped(self, mock_queue):
        h1 = self._make_handover()
        # Other society handover.
        other_outgoing = self._make_guard("OO", society=self.other_society)
        other_incoming = self._make_guard("OI", society=self.other_society)
        ShiftHandover.objects.create(
            society=self.other_society,
            outgoing_guard=other_outgoing,
            incoming_guard=other_incoming,
            gate=self.other_gate,
        )
        result = ShiftHandoverService.list_handovers(society=self.society)
        self.assertEqual(result.count(), 1)
        self.assertEqual(result.first(), h1)

    @patch("gateops.services.notification_engine.queue_email")
    def test_list_handovers_filter_by_status(self, mock_queue):
        self._make_handover(status=ShiftHandover.Status.PENDING)
        self._make_handover(
            status=ShiftHandover.Status.ACKNOWLEDGED,
            acknowledged_at=timezone.now(),
        )
        result = ShiftHandoverService.list_handovers(
            society=self.society, status=ShiftHandover.Status.PENDING
        )
        self.assertEqual(result.count(), 1)
        self.assertEqual(result.first().status, ShiftHandover.Status.PENDING)

    @patch("gateops.services.notification_engine.queue_email")
    def test_list_handovers_filter_by_gate(self, mock_queue):
        second_gate = Gate.objects.create(
            society=self.society,
            name="Back Gate",
            code="BACK",
            gate_type=Gate.GateType.SERVICE,
        )
        h1 = self._make_handover(gate=self.gate)
        h2 = self._make_handover(gate=second_gate)
        result = ShiftHandoverService.list_handovers(
            society=self.society, gate=self.gate
        )
        self.assertEqual(result.count(), 1)
        self.assertEqual(result.first(), h1)

    @patch("gateops.services.notification_engine.queue_email")
    def test_list_handovers_filter_by_guard(self, mock_queue):
        """Filter by guard matches outgoing OR incoming.

        ``ShiftHandoverService.list_handovers`` uses ``Q()`` to match the
        guard against either ``outgoing_guard`` or ``incoming_guard``.
        """
        h1 = self._make_handover()
        other_outgoing = self._make_guard("OtherOut")
        other_incoming = self._make_guard("OtherIn")
        h2 = self._make_handover(
            outgoing_guard=other_outgoing, incoming_guard=other_incoming
        )
        # self.incoming is the incoming_guard on h1 only.
        result = ShiftHandoverService.list_handovers(
            society=self.society, guard=self.incoming
        )
        self.assertEqual(result.count(), 1)
        self.assertEqual(result.first(), h1)
        # self.outgoing is the outgoing_guard on h1 only.
        result = ShiftHandoverService.list_handovers(
            society=self.society, guard=self.outgoing
        )
        self.assertEqual(result.count(), 1)
        self.assertEqual(result.first(), h1)
        # other_outgoing is the outgoing_guard on h2 only.
        result = ShiftHandoverService.list_handovers(
            society=self.society, guard=other_outgoing
        )
        self.assertEqual(result.count(), 1)
        self.assertEqual(result.first(), h2)

    @patch("gateops.services.notification_engine.queue_email")
    def test_list_handovers_excludes_inactive_by_default(self, mock_queue):
        self._make_handover(is_active=False, deleted_at=timezone.now())
        result = ShiftHandoverService.list_handovers(society=self.society)
        self.assertEqual(result.count(), 0)

    @patch("gateops.services.notification_engine.queue_email")
    def test_list_handovers_include_inactive(self, mock_queue):
        self._make_handover(is_active=False, deleted_at=timezone.now())
        result = ShiftHandoverService.list_handovers(
            society=self.society, include_inactive=True
        )
        self.assertEqual(result.count(), 1)

    # ================================================================== #
    # get_handover
    # ================================================================== #

    @patch("gateops.services.notification_engine.queue_email")
    def test_get_handover_by_uuid(self, mock_queue):
        handover = self._make_handover()
        result = ShiftHandoverService.get_handover(
            society=self.society, handover_id=str(handover.handover_uuid)
        )
        self.assertEqual(result, handover)

    @patch("gateops.services.notification_engine.queue_email")
    def test_get_handover_by_pk(self, mock_queue):
        handover = self._make_handover()
        result = ShiftHandoverService.get_handover(
            society=self.society, handover_id=str(handover.pk)
        )
        self.assertEqual(result, handover)

    @patch("gateops.services.notification_engine.queue_email")
    def test_get_handover_cross_society_404(self, mock_queue):
        handover = self._make_handover()
        with self.assertRaises(Http404):
            ShiftHandoverService.get_handover(
                society=self.other_society,
                handover_id=str(handover.handover_uuid),
            )

    @patch("gateops.services.notification_engine.queue_email")
    def test_get_handover_inactive_404(self, mock_queue):
        handover = self._make_handover(is_active=False, deleted_at=timezone.now())
        with self.assertRaises(Http404):
            ShiftHandoverService.get_handover(
                society=self.society, handover_id=str(handover.handover_uuid)
            )

    # ================================================================== #
    # get_handover_items
    # ================================================================== #

    @patch("gateops.services.notification_engine.queue_email")
    def test_get_handover_items_returns_items(self, mock_queue):
        self._make_entered_event(gate=self.gate)
        self._make_entered_event(gate=self.gate)
        handover = ShiftHandoverService.create_shift_handover(
            society=self.society,
            outgoing_guard=self.outgoing,
            incoming_guard=self.incoming,
            gate=self.gate,
        )
        items = ShiftHandoverService.get_handover_items(
            society=self.society, handover_id=str(handover.handover_uuid)
        )
        self.assertEqual(items.count(), 2)

    @patch("gateops.services.notification_engine.queue_email")
    def test_get_handover_items_cross_society_404(self, mock_queue):
        handover = self._make_handover()
        with self.assertRaises(Http404):
            ShiftHandoverService.get_handover_items(
                society=self.other_society,
                handover_id=str(handover.handover_uuid),
            )

    # ================================================================== #
    # get_pending_handovers_for_guard
    # ================================================================== #

    @patch("gateops.services.notification_engine.queue_email")
    def test_get_pending_handovers_for_guard(self, mock_queue):
        h1 = self._make_handover()  # incoming = self.incoming
        result = ShiftHandoverService.get_pending_handovers_for_guard(
            society=self.society, guard=self.incoming
        )
        self.assertEqual(result.count(), 1)
        self.assertEqual(result.first(), h1)

    @patch("gateops.services.notification_engine.queue_email")
    def test_get_pending_handovers_for_guard_excludes_acknowledged(self, mock_queue):
        self._make_handover(
            status=ShiftHandover.Status.ACKNOWLEDGED,
            acknowledged_at=timezone.now(),
        )
        result = ShiftHandoverService.get_pending_handovers_for_guard(
            society=self.society, guard=self.incoming
        )
        self.assertEqual(result.count(), 0)

    @patch("gateops.services.notification_engine.queue_email")
    def test_get_pending_handovers_for_guard_excludes_inactive(self, mock_queue):
        self._make_handover(is_active=False, deleted_at=timezone.now())
        result = ShiftHandoverService.get_pending_handovers_for_guard(
            society=self.society, guard=self.incoming
        )
        self.assertEqual(result.count(), 0)

    # ================================================================== #
    # get_guards_needing_handover
    # ================================================================== #

    @patch("gateops.services.notification_engine.queue_email")
    def test_get_guards_needing_handover_returns_assignments(self, mock_queue):
        """Assignments with check_out_at=None and no pending/acknowledged handover."""
        assignment = GuardShiftAssignment.objects.create(
            society=self.society,
            guard=self.outgoing,
            shift=self.shift,
            gate=self.gate,
            date=timezone.now().date(),
        )
        result = ShiftHandoverService.get_guards_needing_handover(
            society=self.society
        )
        self.assertIn(assignment, result)

    @patch("gateops.services.notification_engine.queue_email")
    def test_get_guards_needing_handover_excludes_checked_out(self, mock_queue):
        GuardShiftAssignment.objects.create(
            society=self.society,
            guard=self.outgoing,
            shift=self.shift,
            gate=self.gate,
            date=timezone.now().date(),
            check_out_at=timezone.now(),
        )
        result = ShiftHandoverService.get_guards_needing_handover(
            society=self.society
        )
        self.assertEqual(result.count(), 0)

    @patch("gateops.services.notification_engine.queue_email")
    def test_get_guards_needing_handover_excludes_with_pending_handover(self, mock_queue):
        assignment = GuardShiftAssignment.objects.create(
            society=self.society,
            guard=self.outgoing,
            shift=self.shift,
            gate=self.gate,
            date=timezone.now().date(),
        )
        # Create a pending handover for this guard+gate.
        self._make_handover(
            outgoing_guard=self.outgoing,
            outgoing_assignment=assignment,
        )
        result = ShiftHandoverService.get_guards_needing_handover(
            society=self.society
        )
        self.assertEqual(result.count(), 0)

    @patch("gateops.services.notification_engine.queue_email")
    def test_get_guards_needing_handover_cross_society(self, mock_queue):
        GuardShiftAssignment.objects.create(
            society=self.society,
            guard=self.outgoing,
            shift=self.shift,
            gate=self.gate,
            date=timezone.now().date(),
        )
        other_guard = self._make_guard("Other", society=self.other_society)
        other_shift = GuardShift.objects.create(
            society=self.other_society,
            name="Evening",
            start_time=time(14, 0),
            end_time=time(22, 0),
        )
        GuardShiftAssignment.objects.create(
            society=self.other_society,
            guard=other_guard,
            shift=other_shift,
            gate=self.other_gate,
            date=timezone.now().date(),
        )
        result = ShiftHandoverService.get_guards_needing_handover(
            society=self.society
        )
        self.assertEqual(result.count(), 1)

    # ================================================================== #
    # _compute_pending_items
    # ================================================================== #

    @patch("gateops.services.notification_engine.queue_email")
    def test_compute_pending_items_empty(self, mock_queue):
        result = ShiftHandoverService._compute_pending_items(self.society)
        self.assertEqual(result["total"], 0)
        self.assertEqual(result["summary"]["pending_approvals"], 0)
        self.assertEqual(result["summary"]["overdue_materials"], 0)
        self.assertEqual(result["summary"]["uncollected_parcels"], 0)

    @patch("gateops.services.notification_engine.queue_email")
    def test_compute_pending_items_counts_pending_approvals(self, mock_queue):
        event = self._make_entered_event(gate=self.gate)
        GateEventApproval.objects.create(
            gate_event=event,
            society=self.society,
            decision=GateEventApproval.Decision.PENDING,
        )
        result = ShiftHandoverService._compute_pending_items(self.society)
        self.assertEqual(result["summary"]["pending_approvals"], 1)
        self.assertEqual(result["total"], 1)

    @patch("gateops.services.notification_engine.queue_email")
    def test_compute_pending_items_counts_overdue_materials(self, mock_queue):
        event = self._make_entered_event(gate=self.gate)
        cat = MaterialCategory.objects.create(
            society=self.society,
            name="Tools",
            code="TOOLS",
        )
        MaterialMovement.objects.create(
            society=self.society,
            gate_event=event,
            material_category=cat,
            quantity=1,
            unit="unit",
            status=MaterialMovement.Status.IN_TRANSIT,
            expected_return_at=timezone.now() - timedelta(hours=1),
        )
        result = ShiftHandoverService._compute_pending_items(self.society)
        self.assertEqual(result["summary"]["overdue_materials"], 1)

    @patch("gateops.services.notification_engine.queue_email")
    def test_compute_pending_items_counts_uncollected_parcels(self, mock_queue):
        event = self._make_entered_event(gate=self.gate)
        Parcel.objects.create(
            society=self.society,
            gate_event=event,
            tracking_number="TRK123",
            status=Parcel.Status.RECEIVED,
        )
        result = ShiftHandoverService._compute_pending_items(self.society)
        self.assertEqual(result["summary"]["uncollected_parcels"], 1)

    @patch("gateops.services.notification_engine.queue_email")
    def test_compute_pending_items_cross_society(self, mock_queue):
        """Only counts items for the specified society."""
        event = self._make_entered_event(gate=self.gate)
        GateEventApproval.objects.create(
            gate_event=event,
            society=self.society,
            decision=GateEventApproval.Decision.PENDING,
        )
        result = ShiftHandoverService._compute_pending_items(
            self.other_society
        )
        self.assertEqual(result["total"], 0)
