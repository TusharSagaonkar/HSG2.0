"""
Test suite for gateops Phase 12 — ExitManagementService.

Covers ExitManagementService:
- process_quick_exit: by UUID, by int PK, cross-society rejection, not-inside
  validation, delegation to record_exit (status becomes EXITED), guard
  assignment, cross-gate exit allowed.
- process_qr_exit: by Pass code, by GateEvent UUID, invalid code, cross-society
  rejection, not-inside validation.
- get_currently_inside: no filters, gate/visitor_category/person/host_unit
  filters, min/max duration filters, is_overstay filter, search filter,
  pagination, empty results.
- get_currently_inside_count: correct count, caching behavior, gate-scoped.
- get_pending_handover_count: with and without guard filter.

Test conventions follow test_contractor_service.py:
- SocietyTestCase base class provides cls.society and cls.user.
- cls.other_society created for cross-society validation tests.
- Seeded Gate and VisitorCategory fetched in setUpTestData().
- _make_* helpers create per-test mutable records.
- _make_entered_event drives an event through invited→arrived→approved→entered.
- @patch("gateops.services.notification_engine.queue_email") mocks notifications.
"""
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.utils import timezone

from core.test_base import SocietyTestCase
from core.test_factories import SocietyFactory
from gateops.models import (
    Gate,
    GateEvent,
    GateEventApproval,
    GuardShift,
    GuardShiftAssignment,
    MaterialCategory,
    MaterialMovement,
    Parcel,
    Pass,
    PassType,
    Person,
    SecurityGuard,
    ShiftHandover,
    VisitorCategory,
)
from gateops.services.exit_management_service import ExitManagementService
from gateops.services.gate_event_lifecycle import GateEventLifecycleService


class ExitManagementServiceTest(SocietyTestCase):
    """Service-level tests for ExitManagementService."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.other_society = SocietyFactory(name="Test Society Beta")
        cls.gate = Gate.objects.get(society=cls.society, code="MAIN")
        cls.other_gate = Gate.objects.get(society=cls.other_society, code="MAIN")
        cls.visitor_cat = VisitorCategory.objects.get(
            society=cls.society, code="GUEST"
        )
        cls.other_visitor_cat = VisitorCategory.objects.get(
            society=cls.other_society, code="GUEST"
        )
        cls.pass_type = PassType.objects.create(
            society=cls.society,
            name="QR Pass",
            code="QRPASS",
            validation_method=PassType.ValidationMethod.QR,
            duration_type=PassType.DurationType.ONE_TIME,
            default_validity_hours=24,
        )

    def setUp(self):
        super().setUp()
        cache.clear()

    # --- helpers ---------------------------------------------------------

    def _make_person(self, society=None, name=None, phone=None):
        return Person.objects.create(
            society=society or self.society,
            name=name or f"Visitor {uuid.uuid4().hex[:6]}",
            phone=phone or f"{uuid.uuid4().int % (10**10):010d}",
        )

    def _make_guard(self, society=None):
        return SecurityGuard.objects.create(
            society=society or self.society,
            name=f"Guard {uuid.uuid4().hex[:6]}",
            phone=f"{uuid.uuid4().int % (10**10):010d}",
            badge_number=f"B{uuid.uuid4().hex[:6]}",
        )

    def _make_entered_event(self, person=None, society=None, gate=None):
        """Drive an event through invited → arrived → approved → entered."""
        soc = society or self.society
        g = gate or self.gate
        vc = (
            self.visitor_cat
            if soc == self.society
            else self.other_visitor_cat
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

    def _make_pass(self, person=None, code=None):
        now = timezone.now()
        return Pass.objects.create(
            society=self.society,
            person=person or self._make_person(),
            pass_type=self.pass_type,
            code=code or f"PASS-{uuid.uuid4().hex[:8].upper()}",
            valid_from=now - timedelta(hours=1),
            valid_until=now + timedelta(hours=24),
        )

    # ================================================================== #
    # process_quick_exit
    # ================================================================== #

    @patch("gateops.services.notification_engine.queue_email")
    def test_process_quick_exit_by_uuid(self, mock_queue):
        event = self._make_entered_event()
        result = ExitManagementService.process_quick_exit(
            society=self.society, gate_event_id=str(event.event_uuid)
        )
        result.refresh_from_db()
        self.assertEqual(result.status, GateEvent.Status.EXITED)
        self.assertIsNotNone(result.exited_at)

    @patch("gateops.services.notification_engine.queue_email")
    def test_process_quick_exit_by_int_pk(self, mock_queue):
        event = self._make_entered_event()
        result = ExitManagementService.process_quick_exit(
            society=self.society, gate_event_id=str(event.pk)
        )
        result.refresh_from_db()
        self.assertEqual(result.status, GateEvent.Status.EXITED)

    @patch("gateops.services.notification_engine.queue_email")
    def test_process_quick_exit_cross_society_rejected(self, mock_queue):
        other_event = self._make_entered_event(society=self.other_society)
        with self.assertRaises(GateEvent.DoesNotExist):
            ExitManagementService.process_quick_exit(
                society=self.society,
                gate_event_id=str(other_event.event_uuid),
            )

    @patch("gateops.services.notification_engine.queue_email")
    def test_process_quick_exit_not_inside_raises(self, mock_queue):
        event = self._make_entered_event()
        # Exit the event first.
        GateEventLifecycleService.record_exit(event)
        event.refresh_from_db()
        with self.assertRaises(ValidationError):
            ExitManagementService.process_quick_exit(
                society=self.society, gate_event_id=str(event.event_uuid)
            )

    @patch("gateops.services.notification_engine.queue_email")
    def test_process_quick_exit_assigns_guard(self, mock_queue):
        event = self._make_entered_event()
        guard = self._make_guard()
        result = ExitManagementService.process_quick_exit(
            society=self.society,
            gate_event_id=str(event.event_uuid),
            guard=guard,
        )
        result.refresh_from_db()
        self.assertEqual(result.guard, guard)

    @patch("gateops.services.notification_engine.queue_email")
    def test_process_quick_exit_cross_gate_allowed(self, mock_queue):
        """A visitor may exit at a gate different from their entry gate."""
        # Create a second gate in the same society.
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

    @patch("gateops.services.notification_engine.queue_email")
    def test_process_quick_exit_invalid_id_raises_does_not_exist(self, mock_queue):
        with self.assertRaises(GateEvent.DoesNotExist):
            ExitManagementService.process_quick_exit(
                society=self.society,
                gate_event_id="not-a-uuid-or-pk",
            )

    # ================================================================== #
    # process_qr_exit
    # ================================================================== #

    @patch("gateops.services.notification_engine.queue_email")
    def test_process_qr_exit_by_pass_code(self, mock_queue):
        person = self._make_person()
        pass_obj = self._make_pass(person=person)
        event = self._make_entered_event(person=person)
        event.pass_ref = pass_obj
        event.save()
        result = ExitManagementService.process_qr_exit(
            society=self.society, qr_code=pass_obj.code
        )
        result.refresh_from_db()
        self.assertEqual(result.status, GateEvent.Status.EXITED)

    @patch("gateops.services.notification_engine.queue_email")
    def test_process_qr_exit_by_event_uuid(self, mock_queue):
        event = self._make_entered_event()
        result = ExitManagementService.process_qr_exit(
            society=self.society, qr_code=str(event.event_uuid)
        )
        result.refresh_from_db()
        self.assertEqual(result.status, GateEvent.Status.EXITED)

    def test_process_qr_exit_invalid_code_raises(self):
        with self.assertRaises(ValidationError):
            ExitManagementService.process_qr_exit(
                society=self.society, qr_code="INVALID-CODE-12345"
            )

    @patch("gateops.services.notification_engine.queue_email")
    def test_process_qr_exit_cross_society_pass_rejected(self, mock_queue):
        """A pass from another society should not resolve an event in this society."""
        other_person = self._make_person(society=self.other_society)
        other_pass_type = PassType.objects.create(
            society=self.other_society,
            name="Other QR Pass",
            code="OQRPASS",
            validation_method=PassType.ValidationMethod.QR,
        )
        now = timezone.now()
        other_pass = Pass.objects.create(
            society=self.other_society,
            person=other_person,
            pass_type=other_pass_type,
            code="OTHER-SOCIETY-PASS",
            valid_from=now - timedelta(hours=1),
            valid_until=now + timedelta(hours=24),
        )
        with self.assertRaises(ValidationError):
            ExitManagementService.process_qr_exit(
                society=self.society, qr_code=other_pass.code
            )

    @patch("gateops.services.notification_engine.queue_email")
    def test_process_qr_exit_not_inside_raises(self, mock_queue):
        person = self._make_person()
        pass_obj = self._make_pass(person=person)
        event = self._make_entered_event(person=person)
        event.pass_ref = pass_obj
        event.save()
        # Exit the event first.
        GateEventLifecycleService.record_exit(event)
        event.refresh_from_db()
        with self.assertRaises(ValidationError):
            ExitManagementService.process_qr_exit(
                society=self.society, qr_code=pass_obj.code
            )

    @patch("gateops.services.notification_engine.queue_email")
    def test_process_qr_exit_inactive_pass_not_resolved(self, mock_queue):
        """An inactive (soft-deleted) pass should not resolve."""
        person = self._make_person()
        pass_obj = self._make_pass(person=person)
        event = self._make_entered_event(person=person)
        event.pass_ref = pass_obj
        event.save()
        # Soft-delete the pass.
        pass_obj.is_active = False
        pass_obj.deleted_at = timezone.now()
        pass_obj.save()
        with self.assertRaises(ValidationError):
            ExitManagementService.process_qr_exit(
                society=self.society, qr_code=pass_obj.code
            )

    # ================================================================== #
    # get_currently_inside
    # ================================================================== #

    @patch("gateops.services.notification_engine.queue_email")
    def test_get_currently_inside_no_filters(self, mock_queue):
        self._make_entered_event()
        self._make_entered_event()
        result = ExitManagementService.get_currently_inside(
            society=self.society
        )
        self.assertEqual(result["total"], 2)
        self.assertEqual(len(result["results"]), 2)
        self.assertEqual(result["page"], 1)
        self.assertEqual(result["page_size"], 50)
        self.assertEqual(result["total_pages"], 1)

    @patch("gateops.services.notification_engine.queue_email")
    def test_get_currently_inside_empty(self, mock_queue):
        result = ExitManagementService.get_currently_inside(
            society=self.society
        )
        self.assertEqual(result["total"], 0)
        self.assertEqual(result["results"], [])

    @patch("gateops.services.notification_engine.queue_email")
    def test_get_currently_inside_filter_by_gate(self, mock_queue):
        second_gate = Gate.objects.create(
            society=self.society,
            name="Back Gate",
            code="BACK",
            gate_type=Gate.GateType.SERVICE,
        )
        e1 = self._make_entered_event(gate=self.gate)
        e2 = self._make_entered_event(gate=second_gate)
        result = ExitManagementService.get_currently_inside(
            society=self.society, filters={"gate_id": self.gate.pk}
        )
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["results"][0]["id"], e1.pk)

    @patch("gateops.services.notification_engine.queue_email")
    def test_get_currently_inside_filter_by_visitor_category(self, mock_queue):
        delivery_cat = VisitorCategory.objects.get(
            society=self.society, code="DELIVERY"
        )
        e1 = self._make_entered_event()
        # Create an event with a different visitor category.
        person2 = self._make_person()
        guard = self._make_guard()
        e2 = GateEventLifecycleService.create_invitation(
            society=self.society,
            visitor_category=delivery_cat,
            person=person2,
            expected_arrival_at=timezone.now(),
            created_by=self.user,
            gate=self.gate,
        )
        GateEventLifecycleService.record_arrival(e2, gate=self.gate, guard=guard)
        e2.refresh_from_db()
        GateEventLifecycleService.approve(e2, approved_by=self.user)
        e2.refresh_from_db()
        GateEventLifecycleService.record_entry(e2, guard=guard)
        e2.refresh_from_db()

        result = ExitManagementService.get_currently_inside(
            society=self.society,
            filters={"visitor_category_id": self.visitor_cat.pk},
        )
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["results"][0]["id"], e1.pk)

    @patch("gateops.services.notification_engine.queue_email")
    def test_get_currently_inside_filter_by_person(self, mock_queue):
        person = self._make_person()
        e1 = self._make_entered_event(person=person)
        self._make_entered_event()  # different person
        result = ExitManagementService.get_currently_inside(
            society=self.society, filters={"person_id": person.pk}
        )
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["results"][0]["id"], e1.pk)

    @patch("gateops.services.notification_engine.queue_email")
    def test_get_currently_inside_filter_by_search_name(self, mock_queue):
        person = self._make_person(name="John Doe")
        self._make_entered_event(person=person)
        result = ExitManagementService.get_currently_inside(
            society=self.society, filters={"search": "John"}
        )
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["results"][0]["person_name"], "John Doe")

    @patch("gateops.services.notification_engine.queue_email")
    def test_get_currently_inside_filter_by_search_phone(self, mock_queue):
        person = self._make_person(phone="9999999999")
        self._make_entered_event(person=person)
        result = ExitManagementService.get_currently_inside(
            society=self.society, filters={"search": "999999"}
        )
        self.assertEqual(result["total"], 1)

    @patch("gateops.services.notification_engine.queue_email")
    def test_get_currently_inside_filter_min_duration(self, mock_queue):
        """Events entered more than N minutes ago."""
        e1 = self._make_entered_event()
        # Manually set entered_at to 30 minutes ago.
        e1.entered_at = timezone.now() - timedelta(minutes=30)
        e1.save()
        e2 = self._make_entered_event()
        # e2 entered just now.
        result = ExitManagementService.get_currently_inside(
            society=self.society, filters={"min_duration_minutes": 15}
        )
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["results"][0]["id"], e1.pk)

    @patch("gateops.services.notification_engine.queue_email")
    def test_get_currently_inside_filter_max_duration(self, mock_queue):
        """Events entered less than N minutes ago."""
        e1 = self._make_entered_event()
        # e1 entered just now.
        e2 = self._make_entered_event()
        e2.entered_at = timezone.now() - timedelta(minutes=30)
        e2.save()
        result = ExitManagementService.get_currently_inside(
            society=self.society, filters={"max_duration_minutes": 15}
        )
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["results"][0]["id"], e1.pk)

    @patch("gateops.services.notification_engine.queue_email")
    def test_get_currently_inside_filter_is_overstay(self, mock_queue):
        e1 = self._make_entered_event()
        # Mark as overstay: auto_close_at in the past.
        # Use .update() to bypass model clean() which rejects past auto_close_at.
        GateEvent.objects.filter(pk=e1.pk).update(
            auto_close_at=timezone.now() - timedelta(minutes=5),
        )
        e1.refresh_from_db()
        e2 = self._make_entered_event()  # not overstay
        result = ExitManagementService.get_currently_inside(
            society=self.society, filters={"is_overstay": True}
        )
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["results"][0]["id"], e1.pk)
        self.assertTrue(result["results"][0]["is_overstay"])

    @patch("gateops.services.notification_engine.queue_email")
    def test_get_currently_inside_pagination(self, mock_queue):
        for _ in range(5):
            self._make_entered_event()
        result = ExitManagementService.get_currently_inside(
            society=self.society, page=1, page_size=2
        )
        self.assertEqual(result["total"], 5)
        self.assertEqual(len(result["results"]), 2)
        self.assertEqual(result["page"], 1)
        self.assertEqual(result["page_size"], 2)
        self.assertEqual(result["total_pages"], 3)

    @patch("gateops.services.notification_engine.queue_email")
    def test_get_currently_inside_pagination_page_2(self, mock_queue):
        for _ in range(5):
            self._make_entered_event()
        result = ExitManagementService.get_currently_inside(
            society=self.society, page=2, page_size=2
        )
        self.assertEqual(result["page"], 2)
        self.assertEqual(len(result["results"]), 2)

    @patch("gateops.services.notification_engine.queue_email")
    def test_get_currently_inside_serialized_fields(self, mock_queue):
        person = self._make_person(name="Jane Smith", phone="1234567890")
        event = self._make_entered_event(person=person)
        result = ExitManagementService.get_currently_inside(
            society=self.society
        )
        serialized = result["results"][0]
        self.assertEqual(serialized["person_name"], "Jane Smith")
        self.assertEqual(serialized["person_phone"], "1234567890")
        self.assertEqual(serialized["event_uuid"], str(event.event_uuid))
        self.assertIn("duration_minutes", serialized)
        self.assertIn("is_overstay", serialized)
        self.assertIn("entered_at", serialized)

    @patch("gateops.services.notification_engine.queue_email")
    def test_get_currently_inside_cross_society_isolation(self, mock_queue):
        self._make_entered_event()
        self._make_entered_event(society=self.other_society)
        result = ExitManagementService.get_currently_inside(
            society=self.society
        )
        self.assertEqual(result["total"], 1)

    # ================================================================== #
    # get_currently_inside_count
    # ================================================================== #

    @patch("gateops.services.notification_engine.queue_email")
    def test_get_currently_inside_count_correct(self, mock_queue):
        self._make_entered_event()
        self._make_entered_event()
        count = ExitManagementService.get_currently_inside_count(
            society=self.society
        )
        self.assertEqual(count, 2)

    @patch("gateops.services.notification_engine.queue_email")
    def test_get_currently_inside_count_zero(self, mock_queue):
        count = ExitManagementService.get_currently_inside_count(
            society=self.society
        )
        self.assertEqual(count, 0)

    @patch("gateops.services.notification_engine.queue_email")
    def test_get_currently_inside_count_caches(self, mock_queue):
        """The count should be cached; a second call without DB changes returns the cached value."""
        self._make_entered_event()
        count1 = ExitManagementService.get_currently_inside_count(
            society=self.society
        )
        # Delete the event; the cached count should still be 1.
        GateEvent.objects.filter(status=GateEvent.Status.ENTERED).delete()
        count2 = ExitManagementService.get_currently_inside_count(
            society=self.society
        )
        self.assertEqual(count1, 1)
        self.assertEqual(count2, 1)  # cached

    @patch("gateops.services.notification_engine.queue_email")
    def test_get_currently_inside_count_gate_scoped(self, mock_queue):
        second_gate = Gate.objects.create(
            society=self.society,
            name="Back Gate",
            code="BACK",
            gate_type=Gate.GateType.SERVICE,
        )
        self._make_entered_event(gate=self.gate)
        self._make_entered_event(gate=second_gate)
        count_main = ExitManagementService.get_currently_inside_count(
            society=self.society, gate=self.gate
        )
        count_back = ExitManagementService.get_currently_inside_count(
            society=self.society, gate=second_gate
        )
        count_all = ExitManagementService.get_currently_inside_count(
            society=self.society
        )
        self.assertEqual(count_main, 1)
        self.assertEqual(count_back, 1)
        self.assertEqual(count_all, 2)

    @patch("gateops.services.notification_engine.queue_email")
    def test_get_currently_inside_count_cross_society(self, mock_queue):
        self._make_entered_event()
        self._make_entered_event(society=self.other_society)
        count = ExitManagementService.get_currently_inside_count(
            society=self.society
        )
        self.assertEqual(count, 1)

    # ================================================================== #
    # get_pending_handover_count
    # ================================================================== #

    def test_get_pending_handover_count_zero(self):
        count = ExitManagementService.get_pending_handover_count(
            society=self.society
        )
        self.assertEqual(count, 0)

    def test_get_pending_handover_count_with_pending(self):
        outgoing = self._make_guard()
        incoming = self._make_guard()
        ShiftHandover.objects.create(
            society=self.society,
            outgoing_guard=outgoing,
            incoming_guard=incoming,
            gate=self.gate,
        )
        count = ExitManagementService.get_pending_handover_count(
            society=self.society
        )
        self.assertEqual(count, 1)

    def test_get_pending_handover_count_filtered_by_guard(self):
        outgoing = self._make_guard()
        incoming = self._make_guard()
        other_incoming = self._make_guard()
        ShiftHandover.objects.create(
            society=self.society,
            outgoing_guard=outgoing,
            incoming_guard=incoming,
            gate=self.gate,
        )
        ShiftHandover.objects.create(
            society=self.society,
            outgoing_guard=self._make_guard(),
            incoming_guard=other_incoming,
            gate=self.gate,
        )
        count = ExitManagementService.get_pending_handover_count(
            society=self.society, guard=incoming
        )
        self.assertEqual(count, 1)

    def test_get_pending_handover_count_excludes_acknowledged(self):
        outgoing = self._make_guard()
        incoming = self._make_guard()
        ShiftHandover.objects.create(
            society=self.society,
            outgoing_guard=outgoing,
            incoming_guard=incoming,
            gate=self.gate,
            status=ShiftHandover.Status.ACKNOWLEDGED,
            acknowledged_at=timezone.now(),
        )
        count = ExitManagementService.get_pending_handover_count(
            society=self.society
        )
        self.assertEqual(count, 0)

    def test_get_pending_handover_count_excludes_inactive(self):
        outgoing = self._make_guard()
        incoming = self._make_guard()
        ShiftHandover.objects.create(
            society=self.society,
            outgoing_guard=outgoing,
            incoming_guard=incoming,
            gate=self.gate,
            is_active=False,
            deleted_at=timezone.now(),
        )
        count = ExitManagementService.get_pending_handover_count(
            society=self.society
        )
        self.assertEqual(count, 0)

    def test_get_pending_handover_count_cross_society(self):
        outgoing = self._make_guard(society=self.other_society)
        incoming = self._make_guard(society=self.other_society)
        ShiftHandover.objects.create(
            society=self.other_society,
            outgoing_guard=outgoing,
            incoming_guard=incoming,
            gate=self.other_gate,
        )
        count = ExitManagementService.get_pending_handover_count(
            society=self.society
        )
        self.assertEqual(count, 0)
