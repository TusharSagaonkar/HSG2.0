"""
Test suite for gateops Phase 12 — Exit Management models.

Covers ShiftHandover and ShiftHandoverItem:
- Creation, field defaults, __str__, UUID field
- clean() validation: cross-society guards, self-handover, state-machine
  consistency (acknowledged_at/disputed_at/dispute_reason)
- Soft-delete (ShiftHandover only; ShiftHandoverItem is immutable)
- Unique constraint on (handover, gate_event) for ShiftHandoverItem
- CASCADE delete of items when parent handover is deleted

Test conventions follow test_contractor_service.py:
- SocietyTestCase base class provides cls.society and cls.user.
- cls.other_society created for cross-society validation tests.
- Seeded Gate and VisitorCategory fetched in setUpTestData().
- _make_* helpers create per-test mutable records.
"""
import uuid
from datetime import time, timedelta
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from core.test_base import SocietyTestCase
from core.test_factories import SocietyFactory
from gateops.models import (
    Gate,
    GateEvent,
    GuardShift,
    GuardShiftAssignment,
    Person,
    SecurityGuard,
    ShiftHandover,
    ShiftHandoverItem,
    VisitorCategory,
)
from gateops.services.gate_event_lifecycle import GateEventLifecycleService


# ---------------------------------------------------------------------------
# ShiftHandover model tests
# ---------------------------------------------------------------------------
class ShiftHandoverModelTest(SocietyTestCase):
    """Model-level tests for ShiftHandover."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.other_society = SocietyFactory(name="Test Society Beta")
        cls.gate = Gate.objects.get(society=cls.society, code="MAIN")
        cls.other_gate = Gate.objects.get(society=cls.other_society, code="MAIN")
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

    def _make_handover(self, **overrides):
        defaults = {
            "society": self.society,
            "outgoing_guard": self.outgoing,
            "incoming_guard": self.incoming,
            "gate": self.gate,
        }
        defaults.update(overrides)
        return ShiftHandover.objects.create(**defaults)

    # --- creation & defaults ---------------------------------------------

    def test_creation_with_all_required_fields(self):
        handover = self._make_handover()
        self.assertEqual(handover.society, self.society)
        self.assertEqual(handover.outgoing_guard, self.outgoing)
        self.assertEqual(handover.incoming_guard, self.incoming)
        self.assertEqual(handover.gate, self.gate)
        self.assertIsNotNone(handover.pk)

    def test_default_values(self):
        handover = self._make_handover()
        self.assertEqual(handover.status, ShiftHandover.Status.PENDING)
        self.assertEqual(handover.inside_count, 0)
        self.assertEqual(handover.pending_items_count, 0)
        self.assertEqual(handover.pending_items_summary, {})
        self.assertEqual(handover.outgoing_notes, "")
        self.assertEqual(handover.incoming_notes, "")
        self.assertEqual(handover.dispute_reason, "")
        self.assertTrue(handover.is_active)
        self.assertIsNone(handover.deleted_at)
        self.assertIsNone(handover.acknowledged_at)
        self.assertIsNone(handover.disputed_at)
        self.assertIsNone(handover.shift)
        self.assertIsNotNone(handover.handed_over_at)
        self.assertIsNotNone(handover.created_at)
        self.assertIsNotNone(handover.updated_at)

    def test_handover_uuid_is_auto_generated_and_unique(self):
        h1 = self._make_handover()
        h2 = self._make_handover(outgoing_guard=self._make_guard("O2"),
                                 incoming_guard=self._make_guard("I2"))
        self.assertIsNotNone(h1.handover_uuid)
        self.assertIsNotNone(h2.handover_uuid)
        self.assertNotEqual(h1.handover_uuid, h2.handover_uuid)

    def test_str_representation(self):
        handover = self._make_handover()
        expected = (
            f"Handover {handover.handover_uuid} — "
            f"{handover.outgoing_guard} → {handover.incoming_guard} "
            f"@ {handover.gate} [{handover.status}]"
        )
        self.assertEqual(str(handover), expected)

    def test_all_status_choices_valid(self):
        for status_value, _label in ShiftHandover.Status.choices:
            guard_a = self._make_guard(f"A_{status_value}")
            guard_b = self._make_guard(f"B_{status_value}")
            handover = ShiftHandover(
                society=self.society,
                outgoing_guard=guard_a,
                incoming_guard=guard_b,
                gate=self.gate,
                status=status_value,
            )
            handover.save()
            self.assertEqual(handover.status, status_value)

    # --- clean() cross-society validation --------------------------------

    def test_clean_rejects_cross_society_outgoing_guard(self):
        other_guard = self._make_guard("Other", society=self.other_society)
        with self.assertRaises(ValidationError):
            self._make_handover(outgoing_guard=other_guard)

    def test_clean_rejects_cross_society_incoming_guard(self):
        other_guard = self._make_guard("Other", society=self.other_society)
        with self.assertRaises(ValidationError):
            self._make_handover(incoming_guard=other_guard)

    def test_clean_rejects_cross_society_gate(self):
        with self.assertRaises(ValidationError):
            self._make_handover(gate=self.other_gate)

    def test_clean_rejects_cross_society_shift(self):
        other_shift = GuardShift.objects.create(
            society=self.other_society,
            name="Evening",
            start_time=time(14, 0),
            end_time=time(22, 0),
        )
        with self.assertRaises(ValidationError):
            self._make_handover(shift=other_shift)

    # --- clean() self-handover -------------------------------------------

    def test_clean_rejects_self_handover(self):
        with self.assertRaises(ValidationError):
            ShiftHandover.objects.create(
                society=self.society,
                outgoing_guard=self.outgoing,
                incoming_guard=self.outgoing,
                gate=self.gate,
            )

    # --- clean() state-machine consistency -------------------------------

    def test_clean_rejects_acknowledged_at_without_acknowledged_status(self):
        with self.assertRaises(ValidationError):
            ShiftHandover.objects.create(
                society=self.society,
                outgoing_guard=self.outgoing,
                incoming_guard=self.incoming,
                gate=self.gate,
                status=ShiftHandover.Status.PENDING,
                acknowledged_at=timezone.now(),
            )

    def test_clean_rejects_disputed_at_without_disputed_status(self):
        with self.assertRaises(ValidationError):
            ShiftHandover.objects.create(
                society=self.society,
                outgoing_guard=self.outgoing,
                incoming_guard=self.incoming,
                gate=self.gate,
                status=ShiftHandover.Status.PENDING,
                disputed_at=timezone.now(),
            )

    def test_clean_rejects_dispute_reason_without_disputed_status(self):
        with self.assertRaises(ValidationError):
            ShiftHandover.objects.create(
                society=self.society,
                outgoing_guard=self.outgoing,
                incoming_guard=self.incoming,
                gate=self.gate,
                status=ShiftHandover.Status.PENDING,
                dispute_reason="Something wrong",
            )

    def test_clean_accepts_acknowledged_at_with_acknowledged_status(self):
        handover = ShiftHandover.objects.create(
            society=self.society,
            outgoing_guard=self.outgoing,
            incoming_guard=self.incoming,
            gate=self.gate,
            status=ShiftHandover.Status.ACKNOWLEDGED,
            acknowledged_at=timezone.now(),
        )
        self.assertEqual(handover.status, ShiftHandover.Status.ACKNOWLEDGED)

    def test_clean_accepts_disputed_at_with_disputed_status(self):
        handover = ShiftHandover.objects.create(
            society=self.society,
            outgoing_guard=self.outgoing,
            incoming_guard=self.incoming,
            gate=self.gate,
            status=ShiftHandover.Status.DISPUTED,
            disputed_at=timezone.now(),
            dispute_reason="Items mismatch",
        )
        self.assertEqual(handover.status, ShiftHandover.Status.DISPUTED)

    # --- soft-delete ------------------------------------------------------

    def test_soft_delete_sets_is_active_false_and_deleted_at(self):
        handover = self._make_handover()
        handover.is_active = False
        handover.deleted_at = timezone.now()
        handover.save()
        handover.refresh_from_db()
        self.assertFalse(handover.is_active)
        self.assertIsNotNone(handover.deleted_at)

    def test_soft_deleted_handover_remains_in_db(self):
        handover = self._make_handover()
        handover.is_active = False
        handover.deleted_at = timezone.now()
        handover.save()
        self.assertTrue(ShiftHandover.objects.filter(pk=handover.pk).exists())


# ---------------------------------------------------------------------------
# ShiftHandoverItem model tests
# ---------------------------------------------------------------------------
class ShiftHandoverItemModelTest(SocietyTestCase):
    """Model-level tests for ShiftHandoverItem."""

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
        self.handover = self._make_handover()
        self.person = self._make_person()
        self.event = self._make_entered_event()

    # --- helpers ---------------------------------------------------------

    def _make_guard(self, label, society=None):
        return SecurityGuard.objects.create(
            society=society or self.society,
            name=f"{label} {uuid.uuid4().hex[:6]}",
            phone=f"{uuid.uuid4().int % (10**10):010d}",
            badge_number=f"B{uuid.uuid4().hex[:6]}",
        )

    def _make_person(self, society=None):
        return Person.objects.create(
            society=society or self.society,
            name=f"Visitor {uuid.uuid4().hex[:6]}",
            phone=f"{uuid.uuid4().int % (10**10):010d}",
        )

    def _make_handover(self, **overrides):
        defaults = {
            "society": self.society,
            "outgoing_guard": self.outgoing,
            "incoming_guard": self.incoming,
            "gate": self.gate,
        }
        defaults.update(overrides)
        return ShiftHandover.objects.create(**defaults)

    def _make_entered_event(self):
        """Drive an event through invited → arrived → approved → entered."""
        guard = SecurityGuard.objects.create(
            society=self.society,
            name=f"Entry Guard {uuid.uuid4().hex[:6]}",
            phone=f"{uuid.uuid4().int % (10**10):010d}",
            badge_number=f"EG{uuid.uuid4().hex[:6]}",
        )
        event = GateEventLifecycleService.create_invitation(
            society=self.society,
            visitor_category=self.visitor_cat,
            person=self.person,
            expected_arrival_at=timezone.now(),
            created_by=self.user,
            gate=self.gate,
        )
        GateEventLifecycleService.record_arrival(event, gate=self.gate, guard=guard)
        event.refresh_from_db()
        GateEventLifecycleService.approve(event, approved_by=self.user)
        event.refresh_from_db()
        GateEventLifecycleService.record_entry(event, guard=guard)
        event.refresh_from_db()
        return event

    def _make_item(self, **overrides):
        defaults = {
            "society": self.society,
            "handover": self.handover,
            "gate_event": self.event,
            "person": self.person,
            "visitor_category": self.visitor_cat,
            "entered_at": self.event.entered_at,
            "gate": self.gate,
        }
        defaults.update(overrides)
        return ShiftHandoverItem.objects.create(**defaults)

    # --- creation & defaults ---------------------------------------------

    def test_creation_with_all_required_fields(self):
        item = self._make_item()
        self.assertEqual(item.society, self.society)
        self.assertEqual(item.handover, self.handover)
        self.assertEqual(item.gate_event, self.event)
        self.assertEqual(item.person, self.person)
        self.assertEqual(item.visitor_category, self.visitor_cat)
        self.assertEqual(item.gate, self.gate)
        self.assertIsNotNone(item.pk)

    def test_default_values(self):
        item = self._make_item()
        self.assertEqual(item.duration_minutes_at_handover, 0)
        self.assertFalse(item.is_overstay)
        self.assertEqual(item.notes, "")
        self.assertIsNotNone(item.created_at)

    def test_str_representation(self):
        item = self._make_item(duration_minutes_at_handover=42)
        expected = f"{self.person.name} inside 42min @ {self.handover}"
        self.assertEqual(str(item), expected)

    def test_str_representation_without_person(self):
        item = self._make_item(person=None)
        expected = f"Unknown inside 0min @ {self.handover}"
        self.assertEqual(str(item), expected)

    # --- clean() cross-society validation --------------------------------

    def test_clean_rejects_cross_society_handover(self):
        other_outgoing = self._make_guard("OO", society=self.other_society)
        other_incoming = self._make_guard("OI", society=self.other_society)
        other_handover = ShiftHandover.objects.create(
            society=self.other_society,
            outgoing_guard=other_outgoing,
            incoming_guard=other_incoming,
            gate=self.other_gate,
        )
        with self.assertRaises(ValidationError):
            ShiftHandoverItem.objects.create(
                society=self.society,
                handover=other_handover,
                gate_event=self.event,
                person=self.person,
            )

    def test_clean_rejects_cross_society_gate_event(self):
        other_person = self._make_person(society=self.other_society)
        other_visitor_cat = VisitorCategory.objects.get(
            society=self.other_society, code="GUEST"
        )
        other_guard = self._make_guard("OG", society=self.other_society)
        other_event = GateEventLifecycleService.create_invitation(
            society=self.other_society,
            visitor_category=other_visitor_cat,
            person=other_person,
            expected_arrival_at=timezone.now(),
            created_by=self.user,
            gate=self.other_gate,
        )
        GateEventLifecycleService.record_arrival(
            other_event, gate=self.other_gate, guard=other_guard
        )
        other_event.refresh_from_db()
        GateEventLifecycleService.approve(other_event, approved_by=self.user)
        other_event.refresh_from_db()
        GateEventLifecycleService.record_entry(other_event, guard=other_guard)
        other_event.refresh_from_db()

        with self.assertRaises(ValidationError):
            ShiftHandoverItem.objects.create(
                society=self.society,
                handover=self.handover,
                gate_event=other_event,
                person=self.person,
            )

    def test_clean_rejects_cross_society_person(self):
        other_person = self._make_person(society=self.other_society)
        with self.assertRaises(ValidationError):
            self._make_item(person=other_person)

    def test_clean_rejects_cross_society_gate(self):
        with self.assertRaises(ValidationError):
            self._make_item(gate=self.other_gate)

    # --- unique constraint -----------------------------------------------

    def test_unique_constraint_per_gate_event(self):
        self._make_item()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ShiftHandoverItem.objects.create(
                    society=self.society,
                    handover=self.handover,
                    gate_event=self.event,
                    person=self.person,
                )

    def test_unique_constraint_allows_null_gate_event(self):
        """Multiple items with gate_event=None should not conflict."""
        item1 = ShiftHandoverItem.objects.create(
            society=self.society,
            handover=self.handover,
            gate_event=None,
            person=self.person,
        )
        item2 = ShiftHandoverItem.objects.create(
            society=self.society,
            handover=self.handover,
            gate_event=None,
            person=None,
        )
        self.assertIsNotNone(item1.pk)
        self.assertIsNotNone(item2.pk)

    # --- CASCADE delete ---------------------------------------------------

    def test_cascade_delete_when_handover_deleted(self):
        item = self._make_item()
        item_pk = item.pk
        self.handover.delete()
        self.assertFalse(
            ShiftHandoverItem.objects.filter(pk=item_pk).exists()
        )

    # --- immutability (no soft-delete) -----------------------------------

    def test_no_soft_delete_fields(self):
        """ShiftHandoverItem has no is_active or deleted_at fields."""
        field_names = {f.name for f in ShiftHandoverItem._meta.get_fields()}
        self.assertNotIn("is_active", field_names)
        self.assertNotIn("deleted_at", field_names)

    # --- FK relationships -------------------------------------------------

    def test_handover_related_name_items(self):
        self._make_item()
        self.assertEqual(self.handover.items.count(), 1)

    def test_gate_event_related_name_handover_items(self):
        self._make_item()
        self.assertEqual(self.event.handover_items.count(), 1)
