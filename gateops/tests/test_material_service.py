"""
Test suite for gateops Phase 7 — Material Movement.

Test conventions:
- SocietyTestCase base class provides cls.society and cls.user (created once
  per class via SocietyFactory with django_get_or_create, avoiding repeated
  bootstrap signal cascades).
- Per-test mutable records (gate events, movements) are created in setUp().
- Seeded MaterialCategory records (INBOUND, OUTBOUND) are fetched in
  setUpTestData().

Covers:
- MaterialMovement model (clean, properties, defaults, soft-delete)
- MaterialService (record_movement, record_return, mark_overdue,
  cancel_movement, generate_gate_pass, get_pending_returns, get_overdue,
  get_by_event, check_and_mark_overdue, _TRANSITIONS)
- Material views (list, record, pending, overdue, detail, return, cancel,
  gate-pass)
"""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.test_base import SocietyTestCase
from core.test_factories import SocietyFactory, UserFactory
from gateops.models import (
    Gate,
    GateEvent,
    GateOpsAuditLog,
    MaterialCategory,
    MaterialMovement,
    VisitorCategory,
)
from gateops.services.material_service import MaterialService
from housing_accounting.selection import SESSION_SELECTED_SOCIETY_ID
from societies.services import create_society


class MaterialMovementModelTest(SocietyTestCase):
    """Model-level tests for MaterialMovement (clean, properties, defaults)."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # Bootstrap seeds 2 MaterialCategories: INBOUND, OUTBOUND.
        cls.inbound_cat = MaterialCategory.objects.get(
            society=cls.society, code="INBOUND"
        )
        cls.outbound_cat = MaterialCategory.objects.get(
            society=cls.society, code="OUTBOUND"
        )
        # Seeded gate and visitor category for GateEvent construction.
        cls.gate = Gate.objects.get(society=cls.society, code="MAIN")
        cls.visitor_cat = VisitorCategory.objects.get(
            society=cls.society, code="GUEST"
        )

    def setUp(self):
        super().setUp()
        self.gate_event = self._make_gate_event()

    # --- helpers ---------------------------------------------------------

    def _make_gate_event(self, **overrides):
        defaults = {
            "society": self.society,
            "gate": self.gate,
            "visitor_category": self.visitor_cat,
            "event_type": GateEvent.EventType.ARRIVAL,
            "status": GateEvent.Status.ARRIVED,
            "direction": GateEvent.Direction.INBOUND,
            "arrived_at": timezone.now(),
        }
        defaults.update(overrides)
        return GateEvent.objects.create(**defaults)

    def _make_movement(self, **overrides):
        defaults = {
            "society": self.society,
            "gate_event": self.gate_event,
            "material_category": self.inbound_cat,
            "quantity": Decimal("5.00"),
            "unit": "bag",
            "owner": "Contractor A",
            "purpose": "Construction work",
        }
        defaults.update(overrides)
        return MaterialMovement.objects.create(**defaults)

    # --- creation & representation ---------------------------------------

    def test_creation_with_all_required_fields(self):
        movement = self._make_movement()
        self.assertEqual(movement.society, self.society)
        self.assertEqual(movement.gate_event, self.gate_event)
        self.assertEqual(movement.material_category, self.inbound_cat)
        self.assertEqual(movement.quantity, Decimal("5.00"))
        self.assertEqual(movement.unit, "bag")
        self.assertEqual(movement.owner, "Contractor A")
        self.assertEqual(movement.purpose, "Construction work")
        self.assertTrue(movement.is_active)
        self.assertIsNone(movement.deleted_at)
        self.assertIsNotNone(movement.created_at)
        self.assertIsNotNone(movement.updated_at)

    def test_str_representation(self):
        movement = self._make_movement(quantity=Decimal("10"), unit="kg")
        self.assertEqual(str(movement), f"10 kg ({self.inbound_cat.code})")

    # --- default values --------------------------------------------------

    def test_default_quantity_is_one(self):
        movement = MaterialMovement.objects.create(
            society=self.society,
            gate_event=self.gate_event,
            material_category=self.inbound_cat,
        )
        self.assertEqual(movement.quantity, Decimal("1"))

    def test_default_unit_is_unit(self):
        movement = MaterialMovement.objects.create(
            society=self.society,
            gate_event=self.gate_event,
            material_category=self.inbound_cat,
        )
        self.assertEqual(movement.unit, "unit")

    def test_default_status_is_in_transit(self):
        movement = MaterialMovement.objects.create(
            society=self.society,
            gate_event=self.gate_event,
            material_category=self.inbound_cat,
        )
        self.assertEqual(movement.status, MaterialMovement.Status.IN_TRANSIT)

    # --- clean() validation ---------------------------------------------

    def test_clean_rejects_zero_quantity(self):
        movement = MaterialMovement(
            society=self.society,
            gate_event=self.gate_event,
            material_category=self.inbound_cat,
            quantity=Decimal("0"),
            unit="bag",
        )
        with self.assertRaises(ValidationError):
            movement.clean()

    def test_clean_rejects_negative_quantity(self):
        movement = MaterialMovement(
            society=self.society,
            gate_event=self.gate_event,
            material_category=self.inbound_cat,
            quantity=Decimal("-1"),
            unit="bag",
        )
        with self.assertRaises(ValidationError):
            movement.clean()

    def test_clean_rejects_none_quantity(self):
        movement = MaterialMovement(
            society=self.society,
            gate_event=self.gate_event,
            material_category=self.inbound_cat,
            quantity=None,
            unit="bag",
        )
        with self.assertRaises(ValidationError):
            movement.clean()

    def test_clean_rejects_blank_unit(self):
        movement = MaterialMovement(
            society=self.society,
            gate_event=self.gate_event,
            material_category=self.inbound_cat,
            quantity=Decimal("1"),
            unit="",
        )
        with self.assertRaises(ValidationError):
            movement.clean()

    def test_clean_rejects_whitespace_only_unit(self):
        movement = MaterialMovement(
            society=self.society,
            gate_event=self.gate_event,
            material_category=self.inbound_cat,
            quantity=Decimal("1"),
            unit="   ",
        )
        with self.assertRaises(ValidationError):
            movement.clean()

    def test_clean_rejects_returned_at_without_returned_status(self):
        movement = MaterialMovement(
            society=self.society,
            gate_event=self.gate_event,
            material_category=self.inbound_cat,
            quantity=Decimal("1"),
            unit="bag",
            returned_at=timezone.now(),
            status=MaterialMovement.Status.IN_TRANSIT,
        )
        with self.assertRaises(ValidationError):
            movement.clean()

    def test_clean_accepts_returned_at_with_returned_status(self):
        movement = MaterialMovement(
            society=self.society,
            gate_event=self.gate_event,
            material_category=self.inbound_cat,
            quantity=Decimal("1"),
            unit="bag",
            returned_at=timezone.now(),
            status=MaterialMovement.Status.RETURNED,
        )
        movement.clean()  # Should not raise.

    # --- is_overdue property --------------------------------------------

    def test_is_overdue_true_when_in_transit_and_past_expected(self):
        movement = self._make_movement(
            status=MaterialMovement.Status.IN_TRANSIT,
            expected_return_at=timezone.now() - timedelta(hours=1),
        )
        self.assertTrue(movement.is_overdue)

    def test_is_overdue_false_when_future_expected(self):
        movement = self._make_movement(
            status=MaterialMovement.Status.IN_TRANSIT,
            expected_return_at=timezone.now() + timedelta(hours=1),
        )
        self.assertFalse(movement.is_overdue)

    def test_is_overdue_false_when_no_expected_return(self):
        movement = self._make_movement(
            status=MaterialMovement.Status.IN_TRANSIT,
            expected_return_at=None,
        )
        self.assertFalse(movement.is_overdue)

    def test_is_overdue_false_when_returned(self):
        movement = self._make_movement(
            status=MaterialMovement.Status.RETURNED,
            expected_return_at=timezone.now() - timedelta(hours=1),
            returned_at=timezone.now(),
        )
        self.assertFalse(movement.is_overdue)

    def test_is_overdue_false_when_overdue_status(self):
        movement = self._make_movement(
            status=MaterialMovement.Status.OVERDUE,
            expected_return_at=timezone.now() - timedelta(hours=1),
        )
        self.assertFalse(movement.is_overdue)

    # --- is_returned property -------------------------------------------

    def test_is_returned_true_when_returned(self):
        movement = self._make_movement(status=MaterialMovement.Status.RETURNED)
        self.assertTrue(movement.is_returned)

    def test_is_returned_false_when_in_transit(self):
        movement = self._make_movement(status=MaterialMovement.Status.IN_TRANSIT)
        self.assertFalse(movement.is_returned)

    def test_is_returned_false_when_overdue(self):
        movement = self._make_movement(status=MaterialMovement.Status.OVERDUE)
        self.assertFalse(movement.is_returned)

    # --- soft-delete behavior -------------------------------------------

    def test_soft_delete_sets_is_active_false_and_deleted_at(self):
        movement = self._make_movement()
        deleted_at = timezone.now()
        movement.is_active = False
        movement.deleted_at = deleted_at
        movement.save(update_fields=["is_active", "deleted_at"])
        movement.refresh_from_db()
        self.assertFalse(movement.is_active)
        self.assertIsNotNone(movement.deleted_at)

    def test_soft_deleted_movement_remains_in_db(self):
        movement = self._make_movement()
        movement.is_active = False
        movement.deleted_at = timezone.now()
        movement.save(update_fields=["is_active", "deleted_at"])
        # The row still exists (soft delete, not hard delete).
        self.assertTrue(
            MaterialMovement.objects.filter(pk=movement.pk).exists()
        )


class MaterialServiceTest(SocietyTestCase):
    """Service-level tests for MaterialService.

    The society and seeded master data are created once per class via
    setUpTestData to avoid re-running the expensive gateops bootstrap signal.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.inbound_cat = MaterialCategory.objects.get(
            society=cls.society, code="INBOUND"
        )
        cls.outbound_cat = MaterialCategory.objects.get(
            society=cls.society, code="OUTBOUND"
        )
        cls.gate = Gate.objects.get(society=cls.society, code="MAIN")
        cls.visitor_cat = VisitorCategory.objects.get(
            society=cls.society, code="GUEST"
        )
        # Second society for cross-society tests (triggers bootstrap once).
        cls.other_society = SocietyFactory(name="Test Society Beta")
        cls.other_inbound_cat = MaterialCategory.objects.get(
            society=cls.other_society, code="INBOUND"
        )
        cls.other_gate = Gate.objects.get(society=cls.other_society, code="MAIN")
        cls.other_visitor_cat = VisitorCategory.objects.get(
            society=cls.other_society, code="GUEST"
        )

    def setUp(self):
        super().setUp()
        self.gate_event = self._make_gate_event()

    # --- helpers ---------------------------------------------------------

    def _make_gate_event(self, **overrides):
        defaults = {
            "society": self.society,
            "gate": self.gate,
            "visitor_category": self.visitor_cat,
            "event_type": GateEvent.EventType.ARRIVAL,
            "status": GateEvent.Status.ARRIVED,
            "direction": GateEvent.Direction.INBOUND,
            "arrived_at": timezone.now(),
        }
        defaults.update(overrides)
        return GateEvent.objects.create(**defaults)

    def _make_other_gate_event(self, **overrides):
        defaults = {
            "society": self.other_society,
            "gate": self.other_gate,
            "visitor_category": self.other_visitor_cat,
            "event_type": GateEvent.EventType.ARRIVAL,
            "status": GateEvent.Status.ARRIVED,
            "direction": GateEvent.Direction.INBOUND,
            "arrived_at": timezone.now(),
        }
        defaults.update(overrides)
        return GateEvent.objects.create(**defaults)

    def _make_movement(self, **overrides):
        defaults = {
            "society": self.society,
            "gate_event": self.gate_event,
            "material_category": self.inbound_cat,
            "quantity": Decimal("5"),
            "unit": "bag",
        }
        defaults.update(overrides)
        return MaterialMovement.objects.create(**defaults)

    # --- record_movement: basic creation --------------------------------

    def test_record_movement_creates_with_correct_fields(self):
        expected_return = timezone.now() + timedelta(hours=2)
        movement = MaterialService.record_movement(
            gate_event=self.gate_event,
            material_category=self.inbound_cat,
            quantity=Decimal("10"),
            unit="kg",
            owner="Owner X",
            purpose="Site work",
            expected_return_at=expected_return,
            actor=self.user,
        )
        self.assertEqual(movement.society, self.society)
        self.assertEqual(movement.gate_event, self.gate_event)
        self.assertEqual(movement.material_category, self.inbound_cat)
        self.assertEqual(movement.quantity, Decimal("10"))
        self.assertEqual(movement.unit, "kg")
        self.assertEqual(movement.owner, "Owner X")
        self.assertEqual(movement.purpose, "Site work")
        self.assertEqual(movement.expected_return_at, expected_return)

    def test_record_movement_sets_status_in_transit(self):
        movement = MaterialService.record_movement(
            gate_event=self.gate_event,
            material_category=self.inbound_cat,
            quantity=Decimal("1"),
            actor=self.user,
        )
        self.assertEqual(movement.status, MaterialMovement.Status.IN_TRANSIT)

    def test_record_movement_denormalizes_society_from_gate_event(self):
        movement = MaterialService.record_movement(
            gate_event=self.gate_event,
            material_category=self.inbound_cat,
            quantity=Decimal("1"),
            actor=self.user,
        )
        self.assertEqual(movement.society_id, self.gate_event.society_id)

    def test_record_movement_creates_audit_log(self):
        movement = MaterialService.record_movement(
            gate_event=self.gate_event,
            material_category=self.inbound_cat,
            quantity=Decimal("1"),
            actor=self.user,
        )
        log = GateOpsAuditLog.objects.filter(
            entity_type="MaterialMovement",
            entity_id=str(movement.pk),
            action=GateOpsAuditLog.Action.CREATE,
        ).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.actor, self.user)
        self.assertIsNotNone(log.after_value)

    def test_record_movement_cross_society_category_raises(self):
        other_event = self._make_other_gate_event()
        with self.assertRaises(ValidationError):
            MaterialService.record_movement(
                gate_event=other_event,
                material_category=self.inbound_cat,
                quantity=Decimal("1"),
            )

    def test_record_movement_zero_quantity_raises(self):
        with self.assertRaises(ValidationError):
            MaterialService.record_movement(
                gate_event=self.gate_event,
                material_category=self.inbound_cat,
                quantity=Decimal("0"),
            )

    def test_record_movement_negative_quantity_raises(self):
        with self.assertRaises(ValidationError):
            MaterialService.record_movement(
                gate_event=self.gate_event,
                material_category=self.inbound_cat,
                quantity=Decimal("-1"),
            )

    def test_record_movement_with_defaults(self):
        movement = MaterialService.record_movement(
            gate_event=self.gate_event,
            material_category=self.inbound_cat,
            quantity=Decimal("1"),
        )
        self.assertEqual(movement.unit, "unit")
        self.assertEqual(movement.owner, "")
        self.assertEqual(movement.purpose, "")
        self.assertIsNone(movement.expected_return_at)

    def test_record_movement_audit_failure_doesnt_block_creation(self):
        """A logging failure must never block a legitimate material operation."""
        with patch.object(
            GateOpsAuditLog,
            "log",
            side_effect=Exception("DB connection lost"),
        ):
            movement = MaterialService.record_movement(
                gate_event=self.gate_event,
                material_category=self.inbound_cat,
                quantity=Decimal("1"),
                actor=self.user,
            )
        # Movement was still created despite the audit log failure.
        self.assertIsNotNone(movement.pk)
        self.assertEqual(movement.status, MaterialMovement.Status.IN_TRANSIT)

    # --- record_return ---------------------------------------------------

    def test_record_return_transitions_to_returned(self):
        movement = self._make_movement(status=MaterialMovement.Status.IN_TRANSIT)
        result = MaterialService.record_return(movement=movement, actor=self.user)
        result.refresh_from_db()
        self.assertEqual(result.status, MaterialMovement.Status.RETURNED)

    def test_record_return_sets_returned_at_default_now(self):
        movement = self._make_movement(status=MaterialMovement.Status.IN_TRANSIT)
        before = timezone.now()
        result = MaterialService.record_return(movement=movement, actor=self.user)
        after = timezone.now()
        result.refresh_from_db()
        self.assertIsNotNone(result.returned_at)
        self.assertLessEqual(before, result.returned_at)
        self.assertLessEqual(result.returned_at, after)

    def test_record_return_sets_returned_at_custom(self):
        movement = self._make_movement(status=MaterialMovement.Status.IN_TRANSIT)
        custom_time = timezone.now() - timedelta(hours=3)
        result = MaterialService.record_return(
            movement=movement, returned_at=custom_time, actor=self.user
        )
        result.refresh_from_db()
        self.assertEqual(result.returned_at, custom_time)

    def test_record_return_creates_audit_log(self):
        movement = self._make_movement(status=MaterialMovement.Status.IN_TRANSIT)
        MaterialService.record_return(movement=movement, actor=self.user)
        log = GateOpsAuditLog.objects.filter(
            entity_type="MaterialMovement",
            entity_id=str(movement.pk),
            action=GateOpsAuditLog.Action.STATE_TRANSITION,
        ).first()
        self.assertIsNotNone(log)
        self.assertIsNotNone(log.before_value)
        self.assertIsNotNone(log.after_value)

    def test_record_return_from_overdue(self):
        movement = self._make_movement(status=MaterialMovement.Status.OVERDUE)
        result = MaterialService.record_return(movement=movement, actor=self.user)
        result.refresh_from_db()
        self.assertEqual(result.status, MaterialMovement.Status.RETURNED)

    def test_record_return_rejects_already_returned(self):
        movement = self._make_movement(status=MaterialMovement.Status.RETURNED)
        with self.assertRaises(ValidationError):
            MaterialService.record_return(movement=movement, actor=self.user)

    def test_record_return_rejects_cancelled(self):
        movement = self._make_movement(status=MaterialMovement.Status.CANCELLED)
        with self.assertRaises(ValidationError):
            MaterialService.record_return(movement=movement, actor=self.user)

    # --- mark_overdue ----------------------------------------------------

    def test_mark_overdue_transitions_to_overdue(self):
        movement = self._make_movement(status=MaterialMovement.Status.IN_TRANSIT)
        result = MaterialService.mark_overdue(movement=movement, actor=self.user)
        result.refresh_from_db()
        self.assertEqual(result.status, MaterialMovement.Status.OVERDUE)

    def test_mark_overdue_creates_audit_log(self):
        movement = self._make_movement(status=MaterialMovement.Status.IN_TRANSIT)
        MaterialService.mark_overdue(movement=movement, actor=self.user)
        log = GateOpsAuditLog.objects.filter(
            entity_type="MaterialMovement",
            entity_id=str(movement.pk),
            action=GateOpsAuditLog.Action.STATE_TRANSITION,
        ).first()
        self.assertIsNotNone(log)

    def test_mark_overdue_rejects_returned(self):
        movement = self._make_movement(status=MaterialMovement.Status.RETURNED)
        with self.assertRaises(ValidationError):
            MaterialService.mark_overdue(movement=movement, actor=self.user)

    def test_mark_overdue_rejects_cancelled(self):
        movement = self._make_movement(status=MaterialMovement.Status.CANCELLED)
        with self.assertRaises(ValidationError):
            MaterialService.mark_overdue(movement=movement, actor=self.user)

    def test_mark_overdue_rejects_already_overdue(self):
        movement = self._make_movement(status=MaterialMovement.Status.OVERDUE)
        with self.assertRaises(ValidationError):
            MaterialService.mark_overdue(movement=movement, actor=self.user)

    # --- cancel_movement -------------------------------------------------

    def test_cancel_from_in_transit(self):
        movement = self._make_movement(status=MaterialMovement.Status.IN_TRANSIT)
        result = MaterialService.cancel_movement(movement=movement, actor=self.user)
        result.refresh_from_db()
        self.assertEqual(result.status, MaterialMovement.Status.CANCELLED)

    def test_cancel_from_overdue(self):
        movement = self._make_movement(status=MaterialMovement.Status.OVERDUE)
        result = MaterialService.cancel_movement(movement=movement, actor=self.user)
        result.refresh_from_db()
        self.assertEqual(result.status, MaterialMovement.Status.CANCELLED)

    def test_cancel_creates_audit_log(self):
        movement = self._make_movement(status=MaterialMovement.Status.IN_TRANSIT)
        MaterialService.cancel_movement(movement=movement, actor=self.user)
        log = GateOpsAuditLog.objects.filter(
            entity_type="MaterialMovement",
            entity_id=str(movement.pk),
            action=GateOpsAuditLog.Action.STATE_TRANSITION,
        ).first()
        self.assertIsNotNone(log)

    def test_cancel_with_reason_creates_additional_audit_log(self):
        movement = self._make_movement(status=MaterialMovement.Status.IN_TRANSIT)
        before_count = GateOpsAuditLog.objects.filter(
            entity_type="MaterialMovement", entity_id=str(movement.pk)
        ).count()
        MaterialService.cancel_movement(
            movement=movement, actor=self.user, reason="Wrong entry"
        )
        after_count = GateOpsAuditLog.objects.filter(
            entity_type="MaterialMovement", entity_id=str(movement.pk)
        ).count()
        # _apply_transition creates 1 log; the reason block creates 1 more.
        self.assertEqual(after_count, before_count + 2)

    def test_cancel_without_reason_creates_single_audit_log(self):
        movement = self._make_movement(status=MaterialMovement.Status.IN_TRANSIT)
        before_count = GateOpsAuditLog.objects.filter(
            entity_type="MaterialMovement", entity_id=str(movement.pk)
        ).count()
        MaterialService.cancel_movement(movement=movement, actor=self.user)
        after_count = GateOpsAuditLog.objects.filter(
            entity_type="MaterialMovement", entity_id=str(movement.pk)
        ).count()
        self.assertEqual(after_count, before_count + 1)

    def test_cancel_rejects_returned(self):
        movement = self._make_movement(status=MaterialMovement.Status.RETURNED)
        with self.assertRaises(ValidationError):
            MaterialService.cancel_movement(movement=movement, actor=self.user)

    def test_cancel_rejects_already_cancelled(self):
        movement = self._make_movement(status=MaterialMovement.Status.CANCELLED)
        with self.assertRaises(ValidationError):
            MaterialService.cancel_movement(movement=movement, actor=self.user)

    # --- generate_gate_pass ----------------------------------------------

    def test_generate_gate_pass_returns_string(self):
        movement = self._make_movement()
        code = MaterialService.generate_gate_pass(movement=movement, actor=self.user)
        self.assertIsInstance(code, str)

    def test_generate_gate_pass_format(self):
        movement = self._make_movement()
        code = MaterialService.generate_gate_pass(movement=movement, actor=self.user)
        prefix = f"GATEPASS-{movement.society_id}-{movement.pk}-"
        self.assertTrue(code.startswith(prefix))
        # The suffix is 8 uppercase hex characters.
        suffix = code[len(prefix):]
        self.assertEqual(len(suffix), 8)
        self.assertTrue(all(c in "0123456789ABCDEF" for c in suffix))

    def test_generate_gate_pass_contains_society_and_pk(self):
        movement = self._make_movement()
        code = MaterialService.generate_gate_pass(movement=movement, actor=self.user)
        self.assertIn(str(movement.society_id), code)
        self.assertIn(str(movement.pk), code)

    def test_generate_gate_pass_no_audit_log(self):
        """generate_gate_pass is a pure computation — no audit entry is created."""
        movement = self._make_movement()
        before = GateOpsAuditLog.objects.filter(
            entity_type="MaterialMovement", entity_id=str(movement.pk)
        ).count()
        MaterialService.generate_gate_pass(movement=movement, actor=self.user)
        after = GateOpsAuditLog.objects.filter(
            entity_type="MaterialMovement", entity_id=str(movement.pk)
        ).count()
        self.assertEqual(after, before)

    # --- get_pending_returns ---------------------------------------------

    def test_get_pending_returns_includes_in_transit_and_overdue(self):
        in_transit = self._make_movement(status=MaterialMovement.Status.IN_TRANSIT)
        overdue = self._make_movement(status=MaterialMovement.Status.OVERDUE)
        result = MaterialService.get_pending_returns(society=self.society)
        pks = [m.pk for m in result]
        self.assertIn(in_transit.pk, pks)
        self.assertIn(overdue.pk, pks)

    def test_get_pending_returns_excludes_returned_and_cancelled(self):
        returned = self._make_movement(status=MaterialMovement.Status.RETURNED)
        cancelled = self._make_movement(status=MaterialMovement.Status.CANCELLED)
        result = MaterialService.get_pending_returns(society=self.society)
        pks = [m.pk for m in result]
        self.assertNotIn(returned.pk, pks)
        self.assertNotIn(cancelled.pk, pks)

    def test_get_pending_returns_society_scoped(self):
        own = self._make_movement(status=MaterialMovement.Status.IN_TRANSIT)
        other_event = self._make_other_gate_event()
        other = MaterialMovement.objects.create(
            society=self.other_society,
            gate_event=other_event,
            material_category=self.other_inbound_cat,
            quantity=Decimal("1"),
            unit="unit",
            status=MaterialMovement.Status.IN_TRANSIT,
        )
        result = MaterialService.get_pending_returns(society=self.society)
        pks = [m.pk for m in result]
        self.assertIn(own.pk, pks)
        self.assertNotIn(other.pk, pks)

    def test_get_pending_returns_excludes_soft_deleted(self):
        movement = self._make_movement(status=MaterialMovement.Status.IN_TRANSIT)
        movement.is_active = False
        movement.deleted_at = timezone.now()
        movement.save(update_fields=["is_active", "deleted_at"])
        result = MaterialService.get_pending_returns(society=self.society)
        self.assertNotIn(movement, result)

    # --- get_overdue -----------------------------------------------------

    def test_get_overdue_returns_past_due_in_transit(self):
        movement = self._make_movement(
            status=MaterialMovement.Status.IN_TRANSIT,
            expected_return_at=timezone.now() - timedelta(hours=1),
        )
        result = MaterialService.get_overdue(society=self.society)
        pks = [m.pk for m in result]
        self.assertIn(movement.pk, pks)

    def test_get_overdue_excludes_future_due(self):
        movement = self._make_movement(
            status=MaterialMovement.Status.IN_TRANSIT,
            expected_return_at=timezone.now() + timedelta(hours=1),
        )
        result = MaterialService.get_overdue(society=self.society)
        pks = [m.pk for m in result]
        self.assertNotIn(movement.pk, pks)

    def test_get_overdue_excludes_no_expected_return(self):
        movement = self._make_movement(
            status=MaterialMovement.Status.IN_TRANSIT,
            expected_return_at=None,
        )
        result = MaterialService.get_overdue(society=self.society)
        pks = [m.pk for m in result]
        self.assertNotIn(movement.pk, pks)

    def test_get_overdue_excludes_already_overdue_status(self):
        """get_overdue only returns IN_TRANSIT; OVERDUE movements are excluded."""
        movement = self._make_movement(
            status=MaterialMovement.Status.OVERDUE,
            expected_return_at=timezone.now() - timedelta(hours=1),
        )
        result = MaterialService.get_overdue(society=self.society)
        pks = [m.pk for m in result]
        self.assertNotIn(movement.pk, pks)

    def test_get_overdue_society_scoped(self):
        own = self._make_movement(
            status=MaterialMovement.Status.IN_TRANSIT,
            expected_return_at=timezone.now() - timedelta(hours=1),
        )
        other_event = self._make_other_gate_event()
        other = MaterialMovement.objects.create(
            society=self.other_society,
            gate_event=other_event,
            material_category=self.other_inbound_cat,
            quantity=Decimal("1"),
            unit="unit",
            status=MaterialMovement.Status.IN_TRANSIT,
            expected_return_at=timezone.now() - timedelta(hours=1),
        )
        result = MaterialService.get_overdue(society=self.society)
        pks = [m.pk for m in result]
        self.assertIn(own.pk, pks)
        self.assertNotIn(other.pk, pks)

    # --- get_by_event ----------------------------------------------------

    def test_get_by_event_returns_movements_for_event(self):
        m1 = self._make_movement()
        m2 = self._make_movement()
        result = MaterialService.get_by_event(gate_event=self.gate_event)
        pks = [m.pk for m in result]
        self.assertIn(m1.pk, pks)
        self.assertIn(m2.pk, pks)

    def test_get_by_event_excludes_other_events(self):
        own = self._make_movement()
        other_event = self._make_gate_event()
        other = self._make_movement(gate_event=other_event)
        result = MaterialService.get_by_event(gate_event=self.gate_event)
        pks = [m.pk for m in result]
        self.assertIn(own.pk, pks)
        self.assertNotIn(other.pk, pks)

    def test_get_by_event_excludes_soft_deleted(self):
        movement = self._make_movement()
        movement.is_active = False
        movement.deleted_at = timezone.now()
        movement.save(update_fields=["is_active", "deleted_at"])
        result = MaterialService.get_by_event(gate_event=self.gate_event)
        pks = [m.pk for m in result]
        self.assertNotIn(movement.pk, pks)

    # --- check_and_mark_overdue ------------------------------------------

    def test_check_and_mark_overdue_marks_past_due(self):
        self._make_movement(
            status=MaterialMovement.Status.IN_TRANSIT,
            expected_return_at=timezone.now() - timedelta(hours=1),
        )
        count = MaterialService.check_and_mark_overdue(society=self.society)
        self.assertEqual(count, 1)

    def test_check_and_mark_overdue_returns_count(self):
        self._make_movement(
            status=MaterialMovement.Status.IN_TRANSIT,
            expected_return_at=timezone.now() - timedelta(hours=1),
        )
        self._make_movement(
            status=MaterialMovement.Status.IN_TRANSIT,
            expected_return_at=timezone.now() - timedelta(hours=2),
        )
        count = MaterialService.check_and_mark_overdue(society=self.society)
        self.assertEqual(count, 2)

    def test_check_and_mark_overdue_scoped_to_society(self):
        own = self._make_movement(
            status=MaterialMovement.Status.IN_TRANSIT,
            expected_return_at=timezone.now() - timedelta(hours=1),
        )
        other_event = self._make_other_gate_event()
        other = MaterialMovement.objects.create(
            society=self.other_society,
            gate_event=other_event,
            material_category=self.other_inbound_cat,
            quantity=Decimal("1"),
            unit="unit",
            status=MaterialMovement.Status.IN_TRANSIT,
            expected_return_at=timezone.now() - timedelta(hours=1),
        )
        count = MaterialService.check_and_mark_overdue(society=self.society)
        self.assertEqual(count, 1)
        # The other society's movement is untouched.
        other.refresh_from_db()
        self.assertEqual(other.status, MaterialMovement.Status.IN_TRANSIT)

    def test_check_and_mark_overdue_all_societies_when_none(self):
        self._make_movement(
            status=MaterialMovement.Status.IN_TRANSIT,
            expected_return_at=timezone.now() - timedelta(hours=1),
        )
        other_event = self._make_other_gate_event()
        MaterialMovement.objects.create(
            society=self.other_society,
            gate_event=other_event,
            material_category=self.other_inbound_cat,
            quantity=Decimal("1"),
            unit="unit",
            status=MaterialMovement.Status.IN_TRANSIT,
            expected_return_at=timezone.now() - timedelta(hours=1),
        )
        count = MaterialService.check_and_mark_overdue(society=None)
        self.assertGreaterEqual(count, 2)

    def test_check_and_mark_overdue_idempotent(self):
        self._make_movement(
            status=MaterialMovement.Status.IN_TRANSIT,
            expected_return_at=timezone.now() - timedelta(hours=1),
        )
        first = MaterialService.check_and_mark_overdue(society=self.society)
        second = MaterialService.check_and_mark_overdue(society=self.society)
        self.assertEqual(first, 1)
        self.assertEqual(second, 0)

    def test_check_and_mark_overdue_skips_future_due(self):
        self._make_movement(
            status=MaterialMovement.Status.IN_TRANSIT,
            expected_return_at=timezone.now() + timedelta(hours=1),
        )
        count = MaterialService.check_and_mark_overdue(society=self.society)
        self.assertEqual(count, 0)

    def test_check_and_mark_overdue_skips_no_expected_return(self):
        self._make_movement(
            status=MaterialMovement.Status.IN_TRANSIT,
            expected_return_at=None,
        )
        count = MaterialService.check_and_mark_overdue(society=self.society)
        self.assertEqual(count, 0)

    # --- _TRANSITIONS state machine -------------------------------------

    def test_transitions_from_in_transit(self):
        allowed = MaterialService._TRANSITIONS[MaterialMovement.Status.IN_TRANSIT]
        self.assertIn(MaterialMovement.Status.RETURNED, allowed)
        self.assertIn(MaterialMovement.Status.OVERDUE, allowed)
        self.assertIn(MaterialMovement.Status.CANCELLED, allowed)

    def test_transitions_from_overdue(self):
        allowed = MaterialService._TRANSITIONS[MaterialMovement.Status.OVERDUE]
        self.assertIn(MaterialMovement.Status.RETURNED, allowed)
        self.assertIn(MaterialMovement.Status.CANCELLED, allowed)
        self.assertNotIn(MaterialMovement.Status.IN_TRANSIT, allowed)

    def test_transitions_terminal_returned(self):
        allowed = MaterialService._TRANSITIONS[MaterialMovement.Status.RETURNED]
        self.assertEqual(allowed, set())

    def test_transitions_terminal_cancelled(self):
        allowed = MaterialService._TRANSITIONS[MaterialMovement.Status.CANCELLED]
        self.assertEqual(allowed, set())


class MaterialViewTest(TestCase):
    """Frontend tests for the Phase 7 material movement views.

    Societies are created once per class in setUpTestData; setUp logs in and
    selects the society so every view resolves the correct tenant.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = UserFactory(password="password")
        # create_society grants the user an active OWNER membership, which the
        # society-selection middleware requires to resolve the active society.
        cls.society = create_society(user=cls.user, name="Material View Society")
        cls.inbound_cat = MaterialCategory.objects.get(
            society=cls.society, code="INBOUND"
        )
        cls.gate = Gate.objects.get(society=cls.society, code="MAIN")
        cls.visitor_cat = VisitorCategory.objects.get(
            society=cls.society, code="GUEST"
        )
        cls.gate_event = GateEvent.objects.create(
            society=cls.society,
            gate=cls.gate,
            visitor_category=cls.visitor_cat,
            event_type=GateEvent.EventType.ARRIVAL,
            status=GateEvent.Status.ARRIVED,
            direction=GateEvent.Direction.INBOUND,
            arrived_at=timezone.now(),
        )
        cls.other_society = create_society(
            user=UserFactory(password="password"), name="Other Material View Society"
        )
        cls.other_inbound_cat = MaterialCategory.objects.get(
            society=cls.other_society, code="INBOUND"
        )
        cls.other_gate = Gate.objects.get(society=cls.other_society, code="MAIN")
        cls.other_visitor_cat = VisitorCategory.objects.get(
            society=cls.other_society, code="GUEST"
        )
        cls.other_gate_event = GateEvent.objects.create(
            society=cls.other_society,
            gate=cls.other_gate,
            visitor_category=cls.other_visitor_cat,
            event_type=GateEvent.EventType.ARRIVAL,
            status=GateEvent.Status.ARRIVED,
            direction=GateEvent.Direction.INBOUND,
            arrived_at=timezone.now(),
        )

    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)
        self._select_society(self.society)
        self.movement = MaterialMovement.objects.create(
            society=self.society,
            gate_event=self.gate_event,
            material_category=self.inbound_cat,
            quantity=Decimal("5"),
            unit="bag",
            status=MaterialMovement.Status.IN_TRANSIT,
        )

    # --- helpers ---------------------------------------------------------

    def _select_society(self, society):
        session = self.client.session
        session[SESSION_SELECTED_SOCIETY_ID] = society.id
        session.save()

    def _make_other_movement(self, **overrides):
        defaults = {
            "society": self.other_society,
            "gate_event": self.other_gate_event,
            "material_category": self.other_inbound_cat,
            "quantity": Decimal("3"),
            "unit": "box",
            "status": MaterialMovement.Status.IN_TRANSIT,
        }
        defaults.update(overrides)
        return MaterialMovement.objects.create(**defaults)

    # --- login required --------------------------------------------------

    def test_list_view_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("gateops:material-list"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.url)

    def test_record_view_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("gateops:material-record"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.url)

    def test_pending_view_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("gateops:material-pending"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.url)

    def test_overdue_view_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("gateops:material-overdue"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.url)

    def test_detail_view_requires_login(self):
        self.client.logout()
        response = self.client.get(
            reverse("gateops:material-detail", kwargs={"pk": self.movement.pk})
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.url)

    # --- list / pending / overdue views ---------------------------------

    def test_list_view_returns_200(self):
        response = self.client.get(reverse("gateops:material-list"))
        self.assertEqual(response.status_code, 200)

    def test_pending_view_returns_200(self):
        response = self.client.get(reverse("gateops:material-pending"))
        self.assertEqual(response.status_code, 200)

    def test_overdue_view_returns_200(self):
        response = self.client.get(reverse("gateops:material-overdue"))
        self.assertEqual(response.status_code, 200)

    # --- record view ----------------------------------------------------

    def test_record_view_get_returns_200(self):
        response = self.client.get(reverse("gateops:material-record"))
        self.assertEqual(response.status_code, 200)

    def test_record_view_post_creates_movement(self):
        response = self.client.post(
            reverse("gateops:material-record"),
            data={
                "gate_event_id": self.gate_event.pk,
                "material_category_id": self.inbound_cat.pk,
                "quantity": "10",
                "unit": "kg",
                "owner": "Owner Y",
                "purpose": "Test purpose",
            },
        )
        self.assertEqual(response.status_code, 302)
        movement = MaterialMovement.objects.get(
            society=self.society,
            gate_event=self.gate_event,
            quantity=Decimal("10"),
            unit="kg",
        )
        self.assertEqual(movement.owner, "Owner Y")
        self.assertEqual(movement.purpose, "Test purpose")
        self.assertEqual(movement.status, MaterialMovement.Status.IN_TRANSIT)
        self.assertEqual(
            response.url,
            reverse("gateops:material-detail", kwargs={"pk": movement.pk}),
        )

    # --- detail view ----------------------------------------------------

    def test_detail_view_200_for_own_society(self):
        response = self.client.get(
            reverse("gateops:material-detail", kwargs={"pk": self.movement.pk})
        )
        self.assertEqual(response.status_code, 200)

    def test_detail_view_404_for_other_society(self):
        other = self._make_other_movement()
        response = self.client.get(
            reverse("gateops:material-detail", kwargs={"pk": other.pk})
        )
        self.assertEqual(response.status_code, 404)

    # --- return view -----------------------------------------------------

    def test_return_view_post_only(self):
        response = self.client.get(
            reverse("gateops:material-return", kwargs={"pk": self.movement.pk})
        )
        self.assertEqual(response.status_code, 405)

    def test_return_view_post_transitions_to_returned(self):
        response = self.client.post(
            reverse("gateops:material-return", kwargs={"pk": self.movement.pk})
        )
        self.assertEqual(response.status_code, 302)
        self.movement.refresh_from_db()
        self.assertEqual(self.movement.status, MaterialMovement.Status.RETURNED)
        self.assertIsNotNone(self.movement.returned_at)

    def test_return_view_404_for_other_society(self):
        other = self._make_other_movement()
        response = self.client.post(
            reverse("gateops:material-return", kwargs={"pk": other.pk})
        )
        self.assertEqual(response.status_code, 404)

    # --- cancel view -----------------------------------------------------

    def test_cancel_view_post_only(self):
        response = self.client.get(
            reverse("gateops:material-cancel", kwargs={"pk": self.movement.pk})
        )
        self.assertEqual(response.status_code, 405)

    def test_cancel_view_post_transitions_to_cancelled(self):
        response = self.client.post(
            reverse("gateops:material-cancel", kwargs={"pk": self.movement.pk}),
            data={"reason": "Duplicate entry"},
        )
        self.assertEqual(response.status_code, 302)
        self.movement.refresh_from_db()
        self.assertEqual(self.movement.status, MaterialMovement.Status.CANCELLED)

    def test_cancel_view_404_for_other_society(self):
        other = self._make_other_movement()
        response = self.client.post(
            reverse("gateops:material-cancel", kwargs={"pk": other.pk})
        )
        self.assertEqual(response.status_code, 404)

    # --- gate pass view --------------------------------------------------

    def test_gate_pass_view_returns_code(self):
        response = self.client.get(
            reverse("gateops:material-gate-pass", kwargs={"pk": self.movement.pk})
        )
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertTrue(body.startswith("GATEPASS-"))
        self.assertIn(str(self.movement.society_id), body)
        self.assertIn(str(self.movement.pk), body)

    def test_gate_pass_view_404_for_other_society(self):
        other = self._make_other_movement()
        response = self.client.get(
            reverse("gateops:material-gate-pass", kwargs={"pk": other.pk})
        )
        self.assertEqual(response.status_code, 404)
