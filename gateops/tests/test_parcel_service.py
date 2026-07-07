"""
Test suite for gateops Phase 8 — Parcel Management.

Test conventions:
- SocietyTestCase base class provides cls.society and cls.user (created once
  per class via SocietyFactory with django_get_or_create, avoiding repeated
  bootstrap signal cascades).
- Per-test mutable records (gate events, parcels) are created in setUp().
- Seeded Gate and VisitorCategory records are fetched in setUpTestData().

Covers:
- Parcel model (clean, properties, defaults, soft-delete)
- ParcelService (receive_parcel, verify_otp, collect_parcel, return_parcel,
  mark_lost, bundle_parcels, get_pending, get_by_event, get_overdue,
  generate_otp, _TRANSITIONS)
- Parcel views (list, receive, pending, overdue, detail, collect, return,
  mark-lost)
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
    Parcel,
    VisitorCategory,
)
from gateops.services.parcel_service import ParcelService
from housing_accounting.selection import SESSION_SELECTED_SOCIETY_ID
from societies.services import create_society


class ParcelModelTest(SocietyTestCase):
    """Model-level tests for Parcel (clean, properties, defaults)."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
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

    def _make_parcel(self, **overrides):
        defaults = {
            "society": self.society,
            "gate_event": self.gate_event,
            "tracking_number": "TRK123456",
            "courier": "DTDC",
        }
        defaults.update(overrides)
        return Parcel.objects.create(**defaults)

    # --- creation & representation ---------------------------------------

    def test_creation_with_all_required_fields(self):
        parcel = self._make_parcel()
        self.assertEqual(parcel.society, self.society)
        self.assertEqual(parcel.gate_event, self.gate_event)
        self.assertEqual(parcel.tracking_number, "TRK123456")
        self.assertEqual(parcel.courier, "DTDC")
        self.assertTrue(parcel.is_active)
        self.assertIsNone(parcel.deleted_at)
        self.assertIsNotNone(parcel.created_at)
        self.assertIsNotNone(parcel.updated_at)

    def test_str_representation(self):
        parcel = self._make_parcel(tracking_number="PKG999")
        self.assertEqual(
            str(parcel), f"Parcel PKG999 ({parcel.get_status_display()})"
        )

    # --- default values --------------------------------------------------

    def test_default_status_is_received(self):
        parcel = self._make_parcel()
        self.assertEqual(parcel.status, Parcel.Status.RECEIVED)

    def test_default_is_cold_storage_false(self):
        parcel = self._make_parcel()
        self.assertFalse(parcel.is_cold_storage)

    def test_default_is_fragile_false(self):
        parcel = self._make_parcel()
        self.assertFalse(parcel.is_fragile)

    def test_default_is_cod_false(self):
        parcel = self._make_parcel()
        self.assertFalse(parcel.is_cod)

    def test_default_is_active_true(self):
        parcel = self._make_parcel()
        self.assertTrue(parcel.is_active)

    # --- clean() validation ---------------------------------------------

    def test_clean_rejects_blank_tracking_number(self):
        parcel = Parcel(
            society=self.society,
            gate_event=self.gate_event,
            tracking_number="",
        )
        with self.assertRaises(ValidationError):
            parcel.clean()

    def test_clean_rejects_whitespace_tracking_number(self):
        parcel = Parcel(
            society=self.society,
            gate_event=self.gate_event,
            tracking_number="   ",
        )
        with self.assertRaises(ValidationError):
            parcel.clean()

    def test_clean_cod_true_requires_positive_amount(self):
        parcel = Parcel(
            society=self.society,
            gate_event=self.gate_event,
            tracking_number="TRK1",
            is_cod=True,
            cod_amount=None,
        )
        with self.assertRaises(ValidationError):
            parcel.clean()

    def test_clean_cod_true_rejects_zero_amount(self):
        parcel = Parcel(
            society=self.society,
            gate_event=self.gate_event,
            tracking_number="TRK1",
            is_cod=True,
            cod_amount=Decimal("0"),
        )
        with self.assertRaises(ValidationError):
            parcel.clean()

    def test_clean_cod_true_rejects_negative_amount(self):
        parcel = Parcel(
            society=self.society,
            gate_event=self.gate_event,
            tracking_number="TRK1",
            is_cod=True,
            cod_amount=Decimal("-1"),
        )
        with self.assertRaises(ValidationError):
            parcel.clean()

    def test_clean_cod_true_accepts_positive_amount(self):
        parcel = Parcel(
            society=self.society,
            gate_event=self.gate_event,
            tracking_number="TRK1",
            is_cod=True,
            cod_amount=Decimal("100"),
        )
        parcel.clean()  # Should not raise.

    def test_clean_cod_false_clears_cod_amount(self):
        parcel = Parcel(
            society=self.society,
            gate_event=self.gate_event,
            tracking_number="TRK1",
            is_cod=False,
            cod_amount=Decimal("100"),
        )
        parcel.clean()
        self.assertIsNone(parcel.cod_amount)

    def test_clean_collected_requires_collected_at(self):
        parcel = Parcel(
            society=self.society,
            gate_event=self.gate_event,
            tracking_number="TRK1",
            status=Parcel.Status.COLLECTED,
            collected_at=None,
        )
        with self.assertRaises(ValidationError):
            parcel.clean()

    def test_clean_collected_accepts_collected_at(self):
        parcel = Parcel(
            society=self.society,
            gate_event=self.gate_event,
            tracking_number="TRK1",
            status=Parcel.Status.COLLECTED,
            collected_at=timezone.now(),
        )
        parcel.clean()  # Should not raise.

    def test_clean_non_collected_clears_collected_by(self):
        parcel = Parcel(
            society=self.society,
            gate_event=self.gate_event,
            tracking_number="TRK1",
            status=Parcel.Status.RECEIVED,
            collected_by=self.user,
        )
        parcel.clean()
        self.assertIsNone(parcel.collected_by)

    # --- is_pending property --------------------------------------------

    def test_is_pending_true_when_received(self):
        parcel = self._make_parcel(status=Parcel.Status.RECEIVED)
        self.assertTrue(parcel.is_pending)

    def test_is_pending_false_when_collected(self):
        parcel = self._make_parcel(
            status=Parcel.Status.COLLECTED, collected_at=timezone.now()
        )
        self.assertFalse(parcel.is_pending)

    def test_is_pending_false_when_returned(self):
        parcel = self._make_parcel(status=Parcel.Status.RETURNED)
        self.assertFalse(parcel.is_pending)

    # --- is_collected property ------------------------------------------

    def test_is_collected_true_when_collected(self):
        parcel = self._make_parcel(
            status=Parcel.Status.COLLECTED, collected_at=timezone.now()
        )
        self.assertTrue(parcel.is_collected)

    def test_is_collected_false_when_received(self):
        parcel = self._make_parcel(status=Parcel.Status.RECEIVED)
        self.assertFalse(parcel.is_collected)

    def test_is_collected_false_when_returned(self):
        parcel = self._make_parcel(status=Parcel.Status.RETURNED)
        self.assertFalse(parcel.is_collected)

    # --- is_terminal property --------------------------------------------

    def test_is_terminal_true_for_collected(self):
        parcel = self._make_parcel(
            status=Parcel.Status.COLLECTED, collected_at=timezone.now()
        )
        self.assertTrue(parcel.is_terminal)

    def test_is_terminal_true_for_returned(self):
        parcel = self._make_parcel(status=Parcel.Status.RETURNED)
        self.assertTrue(parcel.is_terminal)

    def test_is_terminal_true_for_lost(self):
        parcel = self._make_parcel(status=Parcel.Status.LOST)
        self.assertTrue(parcel.is_terminal)

    def test_is_terminal_false_for_received(self):
        parcel = self._make_parcel(status=Parcel.Status.RECEIVED)
        self.assertFalse(parcel.is_terminal)

    # --- soft-delete behavior -------------------------------------------

    def test_soft_delete_sets_is_active_false_and_deleted_at(self):
        parcel = self._make_parcel()
        deleted_at = timezone.now()
        parcel.is_active = False
        parcel.deleted_at = deleted_at
        parcel.save(update_fields=["is_active", "deleted_at"])
        parcel.refresh_from_db()
        self.assertFalse(parcel.is_active)
        self.assertIsNotNone(parcel.deleted_at)

    def test_soft_deleted_parcel_remains_in_db(self):
        parcel = self._make_parcel()
        parcel.is_active = False
        parcel.deleted_at = timezone.now()
        parcel.save(update_fields=["is_active", "deleted_at"])
        # The row still exists (soft delete, not hard delete).
        self.assertTrue(Parcel.objects.filter(pk=parcel.pk).exists())


class ParcelServiceTest(SocietyTestCase):
    """Service-level tests for ParcelService.

    The society and seeded master data are created once per class via
    setUpTestData to avoid re-running the expensive gateops bootstrap signal.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.gate = Gate.objects.get(society=cls.society, code="MAIN")
        cls.visitor_cat = VisitorCategory.objects.get(
            society=cls.society, code="GUEST"
        )
        # Second society for cross-society tests (triggers bootstrap once).
        cls.other_society = SocietyFactory(name="Test Society Beta")
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

    def _make_parcel(self, **overrides):
        """Create a RECEIVED parcel via the service (with OTP + stored_at)."""
        defaults = {
            "gate_event": self.gate_event,
            "tracking_number": "TRK123",
            "courier": "DTDC",
            "actor": self.user,
        }
        defaults.update(overrides)
        return ParcelService.receive_parcel(**defaults)

    def _make_other_parcel(self, **overrides):
        defaults = {
            "gate_event": self._make_other_gate_event(),
            "tracking_number": "OTHER-TRK",
            "actor": self.user,
        }
        defaults.update(overrides)
        return ParcelService.receive_parcel(**defaults)

    # --- receive_parcel: basic creation ---------------------------------

    def test_receive_parcel_creates_with_correct_fields(self):
        parcel = ParcelService.receive_parcel(
            gate_event=self.gate_event,
            tracking_number="PKG001",
            courier="BlueDart",
            is_cold_storage=True,
            is_fragile=True,
            is_cod=True,
            cod_amount=Decimal("250"),
            actor=self.user,
        )
        self.assertEqual(parcel.society, self.society)
        self.assertEqual(parcel.gate_event, self.gate_event)
        self.assertEqual(parcel.tracking_number, "PKG001")
        self.assertEqual(parcel.courier, "BlueDart")
        self.assertTrue(parcel.is_cold_storage)
        self.assertTrue(parcel.is_fragile)
        self.assertTrue(parcel.is_cod)
        self.assertEqual(parcel.cod_amount, Decimal("250"))

    def test_receive_parcel_sets_status_received(self):
        parcel = self._make_parcel()
        self.assertEqual(parcel.status, Parcel.Status.RECEIVED)

    def test_receive_parcel_denormalizes_society_from_gate_event(self):
        parcel = self._make_parcel()
        self.assertEqual(parcel.society_id, self.gate_event.society_id)

    def test_receive_parcel_generates_otp_code(self):
        parcel = self._make_parcel()
        self.assertTrue(parcel.otp_code)

    def test_receive_parcel_otp_is_six_digits_by_default(self):
        parcel = self._make_parcel()
        self.assertEqual(len(parcel.otp_code), 6)
        self.assertTrue(parcel.otp_code.isdigit())

    def test_receive_parcel_sets_stored_at_to_now(self):
        before = timezone.now()
        parcel = self._make_parcel()
        after = timezone.now()
        self.assertIsNotNone(parcel.stored_at)
        self.assertLessEqual(before, parcel.stored_at)
        self.assertLessEqual(parcel.stored_at, after)

    def test_receive_parcel_creates_audit_log(self):
        parcel = self._make_parcel()
        log = GateOpsAuditLog.objects.filter(
            entity_type="Parcel",
            entity_id=str(parcel.pk),
            action=GateOpsAuditLog.Action.CREATE,
        ).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.actor, self.user)
        self.assertIsNotNone(log.after_value)

    def test_receive_parcel_audit_failure_doesnt_block_creation(self):
        """A logging failure must never block a legitimate parcel operation."""
        with patch.object(
            GateOpsAuditLog,
            "log",
            side_effect=Exception("DB connection lost"),
        ):
            parcel = ParcelService.receive_parcel(
                gate_event=self.gate_event,
                tracking_number="TRK-AUDIT-FAIL",
                actor=self.user,
            )
        # Parcel was still created despite the audit log failure.
        self.assertIsNotNone(parcel.pk)
        self.assertEqual(parcel.status, Parcel.Status.RECEIVED)

    def test_receive_parcel_rejects_blank_tracking_number(self):
        with self.assertRaises(ValidationError):
            ParcelService.receive_parcel(
                gate_event=self.gate_event,
                tracking_number="",
            )

    def test_receive_parcel_rejects_whitespace_tracking_number(self):
        with self.assertRaises(ValidationError):
            ParcelService.receive_parcel(
                gate_event=self.gate_event,
                tracking_number="   ",
            )

    def test_receive_parcel_cod_requires_positive_amount(self):
        with self.assertRaises(ValidationError):
            ParcelService.receive_parcel(
                gate_event=self.gate_event,
                tracking_number="TRK-COD",
                is_cod=True,
                cod_amount=None,
            )

    def test_receive_parcel_cod_rejects_zero_amount(self):
        with self.assertRaises(ValidationError):
            ParcelService.receive_parcel(
                gate_event=self.gate_event,
                tracking_number="TRK-COD",
                is_cod=True,
                cod_amount=Decimal("0"),
            )

    def test_receive_parcel_non_cod_clears_cod_amount(self):
        parcel = ParcelService.receive_parcel(
            gate_event=self.gate_event,
            tracking_number="TRK-NOCOD",
            is_cod=False,
            cod_amount=Decimal("100"),
        )
        self.assertIsNone(parcel.cod_amount)

    def test_receive_parcel_with_defaults(self):
        parcel = ParcelService.receive_parcel(
            gate_event=self.gate_event,
            tracking_number="TRK-DEF",
        )
        self.assertEqual(parcel.courier, "")
        self.assertFalse(parcel.is_cold_storage)
        self.assertFalse(parcel.is_fragile)
        self.assertFalse(parcel.is_cod)
        self.assertIsNone(parcel.cod_amount)

    # --- verify_otp -----------------------------------------------------

    def test_verify_otp_returns_true_when_match(self):
        parcel = self._make_parcel()
        result = ParcelService.verify_otp(
            parcel=parcel, otp_code=parcel.otp_code
        )
        self.assertTrue(result)

    def test_verify_otp_returns_false_when_mismatch(self):
        parcel = self._make_parcel()
        result = ParcelService.verify_otp(
            parcel=parcel, otp_code="000000"
        )
        self.assertFalse(result)

    def test_verify_otp_raises_when_not_received(self):
        parcel = self._make_parcel()
        # Transition to COLLECTED so verify_otp rejects it.
        ParcelService.collect_parcel(
            parcel=parcel,
            otp_code=parcel.otp_code,
            collected_by=self.user,
            actor=self.user,
        )
        with self.assertRaises(ValidationError):
            ParcelService.verify_otp(
                parcel=parcel, otp_code=parcel.otp_code
            )

    def test_verify_otp_returns_false_when_no_otp(self):
        # Create a parcel directly with no OTP (bypasses service).
        parcel = Parcel.objects.create(
            society=self.society,
            gate_event=self.gate_event,
            tracking_number="NO-OTP",
            status=Parcel.Status.RECEIVED,
        )
        result = ParcelService.verify_otp(parcel=parcel, otp_code="123456")
        self.assertFalse(result)

    # --- collect_parcel --------------------------------------------------

    def test_collect_parcel_transitions_to_collected(self):
        parcel = self._make_parcel()
        result = ParcelService.collect_parcel(
            parcel=parcel,
            otp_code=parcel.otp_code,
            collected_by=self.user,
            actor=self.user,
        )
        result.refresh_from_db()
        self.assertEqual(result.status, Parcel.Status.COLLECTED)

    def test_collect_parcel_sets_collected_by(self):
        parcel = self._make_parcel()
        result = ParcelService.collect_parcel(
            parcel=parcel,
            otp_code=parcel.otp_code,
            collected_by=self.user,
            actor=self.user,
        )
        result.refresh_from_db()
        self.assertEqual(result.collected_by, self.user)

    def test_collect_parcel_sets_collected_at(self):
        parcel = self._make_parcel()
        before = timezone.now()
        result = ParcelService.collect_parcel(
            parcel=parcel,
            otp_code=parcel.otp_code,
            collected_by=self.user,
            actor=self.user,
        )
        after = timezone.now()
        result.refresh_from_db()
        self.assertIsNotNone(result.collected_at)
        self.assertLessEqual(before, result.collected_at)
        self.assertLessEqual(result.collected_at, after)

    def test_collect_parcel_creates_audit_log(self):
        parcel = self._make_parcel()
        ParcelService.collect_parcel(
            parcel=parcel,
            otp_code=parcel.otp_code,
            collected_by=self.user,
            actor=self.user,
        )
        log = GateOpsAuditLog.objects.filter(
            entity_type="Parcel",
            entity_id=str(parcel.pk),
            action=GateOpsAuditLog.Action.STATE_TRANSITION,
        ).first()
        self.assertIsNotNone(log)
        self.assertIsNotNone(log.before_value)
        self.assertIsNotNone(log.after_value)

    def test_collect_parcel_rejects_invalid_otp(self):
        parcel = self._make_parcel()
        with self.assertRaises(ValidationError):
            ParcelService.collect_parcel(
                parcel=parcel,
                otp_code="000000",
                collected_by=self.user,
                actor=self.user,
            )

    def test_collect_parcel_rejects_collected(self):
        parcel = self._make_parcel()
        # First collection succeeds.
        ParcelService.collect_parcel(
            parcel=parcel,
            otp_code=parcel.otp_code,
            collected_by=self.user,
            actor=self.user,
        )
        # Second collection fails (verify_otp raises because not RECEIVED).
        with self.assertRaises(ValidationError):
            ParcelService.collect_parcel(
                parcel=parcel,
                otp_code=parcel.otp_code,
                collected_by=self.user,
                actor=self.user,
            )

    def test_collect_parcel_rejects_returned(self):
        parcel = self._make_parcel()
        ParcelService.return_parcel(parcel=parcel, actor=self.user)
        with self.assertRaises(ValidationError):
            ParcelService.collect_parcel(
                parcel=parcel,
                otp_code=parcel.otp_code,
                collected_by=self.user,
                actor=self.user,
            )

    def test_collect_parcel_rejects_lost(self):
        parcel = self._make_parcel()
        ParcelService.mark_lost(parcel=parcel, actor=self.user)
        with self.assertRaises(ValidationError):
            ParcelService.collect_parcel(
                parcel=parcel,
                otp_code=parcel.otp_code,
                collected_by=self.user,
                actor=self.user,
            )

    # --- return_parcel ---------------------------------------------------

    def test_return_parcel_transitions_to_returned(self):
        parcel = self._make_parcel()
        result = ParcelService.return_parcel(parcel=parcel, actor=self.user)
        result.refresh_from_db()
        self.assertEqual(result.status, Parcel.Status.RETURNED)

    def test_return_parcel_creates_audit_log(self):
        parcel = self._make_parcel()
        ParcelService.return_parcel(parcel=parcel, actor=self.user)
        log = GateOpsAuditLog.objects.filter(
            entity_type="Parcel",
            entity_id=str(parcel.pk),
            action=GateOpsAuditLog.Action.STATE_TRANSITION,
        ).first()
        self.assertIsNotNone(log)

    def test_return_parcel_with_reason_creates_additional_audit_log(self):
        parcel = self._make_parcel()
        before_count = GateOpsAuditLog.objects.filter(
            entity_type="Parcel", entity_id=str(parcel.pk)
        ).count()
        ParcelService.return_parcel(
            parcel=parcel, actor=self.user, reason="Refused by resident"
        )
        after_count = GateOpsAuditLog.objects.filter(
            entity_type="Parcel", entity_id=str(parcel.pk)
        ).count()
        # _apply_transition creates 1 log; the reason block creates 1 more.
        self.assertEqual(after_count, before_count + 2)

    def test_return_parcel_without_reason_creates_single_audit_log(self):
        parcel = self._make_parcel()
        before_count = GateOpsAuditLog.objects.filter(
            entity_type="Parcel", entity_id=str(parcel.pk)
        ).count()
        ParcelService.return_parcel(parcel=parcel, actor=self.user)
        after_count = GateOpsAuditLog.objects.filter(
            entity_type="Parcel", entity_id=str(parcel.pk)
        ).count()
        self.assertEqual(after_count, before_count + 1)

    def test_return_parcel_rejects_collected(self):
        parcel = self._make_parcel()
        ParcelService.collect_parcel(
            parcel=parcel,
            otp_code=parcel.otp_code,
            collected_by=self.user,
            actor=self.user,
        )
        with self.assertRaises(ValidationError):
            ParcelService.return_parcel(parcel=parcel, actor=self.user)

    def test_return_parcel_rejects_returned(self):
        parcel = self._make_parcel()
        ParcelService.return_parcel(parcel=parcel, actor=self.user)
        with self.assertRaises(ValidationError):
            ParcelService.return_parcel(parcel=parcel, actor=self.user)

    def test_return_parcel_rejects_lost(self):
        parcel = self._make_parcel()
        ParcelService.mark_lost(parcel=parcel, actor=self.user)
        with self.assertRaises(ValidationError):
            ParcelService.return_parcel(parcel=parcel, actor=self.user)

    # --- mark_lost -------------------------------------------------------

    def test_mark_lost_transitions_to_lost(self):
        parcel = self._make_parcel()
        result = ParcelService.mark_lost(parcel=parcel, actor=self.user)
        result.refresh_from_db()
        self.assertEqual(result.status, Parcel.Status.LOST)

    def test_mark_lost_creates_audit_log(self):
        parcel = self._make_parcel()
        ParcelService.mark_lost(parcel=parcel, actor=self.user)
        log = GateOpsAuditLog.objects.filter(
            entity_type="Parcel",
            entity_id=str(parcel.pk),
            action=GateOpsAuditLog.Action.STATE_TRANSITION,
        ).first()
        self.assertIsNotNone(log)

    def test_mark_lost_rejects_collected(self):
        parcel = self._make_parcel()
        ParcelService.collect_parcel(
            parcel=parcel,
            otp_code=parcel.otp_code,
            collected_by=self.user,
            actor=self.user,
        )
        with self.assertRaises(ValidationError):
            ParcelService.mark_lost(parcel=parcel, actor=self.user)

    def test_mark_lost_rejects_returned(self):
        parcel = self._make_parcel()
        ParcelService.return_parcel(parcel=parcel, actor=self.user)
        with self.assertRaises(ValidationError):
            ParcelService.mark_lost(parcel=parcel, actor=self.user)

    def test_mark_lost_rejects_lost(self):
        parcel = self._make_parcel()
        ParcelService.mark_lost(parcel=parcel, actor=self.user)
        with self.assertRaises(ValidationError):
            ParcelService.mark_lost(parcel=parcel, actor=self.user)

    # --- bundle_parcels --------------------------------------------------

    def test_bundle_parcels_returns_received_only(self):
        p1 = self._make_parcel(tracking_number="B1")
        p2 = self._make_parcel(tracking_number="B2")
        bundled = ParcelService.bundle_parcels(parcels=[p1, p2], actor=self.user)
        pks = [p.pk for p in bundled]
        self.assertIn(p1.pk, pks)
        self.assertIn(p2.pk, pks)

    def test_bundle_parcels_filters_non_received(self):
        received = self._make_parcel(tracking_number="R1")
        collected = self._make_parcel(tracking_number="C1")
        ParcelService.collect_parcel(
            parcel=collected,
            otp_code=collected.otp_code,
            collected_by=self.user,
            actor=self.user,
        )
        bundled = ParcelService.bundle_parcels(
            parcels=[received, collected], actor=self.user
        )
        pks = [p.pk for p in bundled]
        self.assertIn(received.pk, pks)
        self.assertNotIn(collected.pk, pks)

    def test_bundle_parcels_creates_audit_log(self):
        p1 = self._make_parcel(tracking_number="A1")
        ParcelService.bundle_parcels(parcels=[p1], actor=self.user)
        log = GateOpsAuditLog.objects.filter(
            entity_type="Parcel",
            action=GateOpsAuditLog.Action.UPDATE,
        ).first()
        self.assertIsNotNone(log)

    def test_bundle_parcels_empty_input_returns_empty(self):
        bundled = ParcelService.bundle_parcels(parcels=[], actor=self.user)
        self.assertEqual(bundled, [])

    def test_bundle_parcels_same_society_only(self):
        own = self._make_parcel(tracking_number="OWN")
        other = self._make_other_parcel(tracking_number="OTH")
        # First parcel is from the main society; the other-society parcel
        # is filtered out.
        bundled = ParcelService.bundle_parcels(
            parcels=[own, other], actor=self.user
        )
        pks = [p.pk for p in bundled]
        self.assertIn(own.pk, pks)
        self.assertNotIn(other.pk, pks)

    # --- get_pending -----------------------------------------------------

    def test_get_pending_returns_received_parcels(self):
        p1 = self._make_parcel(tracking_number="P1")
        result = ParcelService.get_pending(society=self.society)
        pks = [p.pk for p in result]
        self.assertIn(p1.pk, pks)

    def test_get_pending_excludes_terminal_statuses(self):
        collected = self._make_parcel(tracking_number="COL")
        returned = self._make_parcel(tracking_number="RET")
        lost = self._make_parcel(tracking_number="LST")
        ParcelService.collect_parcel(
            parcel=collected,
            otp_code=collected.otp_code,
            collected_by=self.user,
            actor=self.user,
        )
        ParcelService.return_parcel(parcel=returned, actor=self.user)
        ParcelService.mark_lost(parcel=lost, actor=self.user)
        result = ParcelService.get_pending(society=self.society)
        pks = [p.pk for p in result]
        self.assertNotIn(collected.pk, pks)
        self.assertNotIn(returned.pk, pks)
        self.assertNotIn(lost.pk, pks)

    def test_get_pending_excludes_soft_deleted(self):
        parcel = self._make_parcel(tracking_number="DEL")
        parcel.is_active = False
        parcel.deleted_at = timezone.now()
        parcel.save(update_fields=["is_active", "deleted_at"])
        result = ParcelService.get_pending(society=self.society)
        self.assertNotIn(parcel, result)

    def test_get_pending_society_scoped(self):
        own = self._make_parcel(tracking_number="OWN")
        other = self._make_other_parcel(tracking_number="OTH")
        result = ParcelService.get_pending(society=self.society)
        pks = [p.pk for p in result]
        self.assertIn(own.pk, pks)
        self.assertNotIn(other.pk, pks)

    def test_get_pending_ordered_by_stored_at_fifo(self):
        # Create two parcels and backdate their stored_at so the oldest
        # surfaces first (FIFO).
        old = self._make_parcel(tracking_number="OLD")
        Parcel.objects.filter(pk=old.pk).update(
            stored_at=timezone.now() - timedelta(hours=2)
        )
        new = self._make_parcel(tracking_number="NEW")
        Parcel.objects.filter(pk=new.pk).update(
            stored_at=timezone.now() - timedelta(hours=1)
        )
        result = ParcelService.get_pending(society=self.society)
        pks = [p.pk for p in result]
        # old should come before new (oldest first).
        self.assertEqual(pks[0], old.pk)
        self.assertEqual(pks[1], new.pk)

    # --- get_by_event ----------------------------------------------------

    def test_get_by_event_returns_parcels_for_event(self):
        p1 = self._make_parcel(tracking_number="E1")
        p2 = self._make_parcel(tracking_number="E2")
        result = ParcelService.get_by_event(gate_event=self.gate_event)
        pks = [p.pk for p in result]
        self.assertIn(p1.pk, pks)
        self.assertIn(p2.pk, pks)

    def test_get_by_event_excludes_other_events(self):
        own = self._make_parcel(tracking_number="OWN")
        other_event = self._make_gate_event()
        other = self._make_parcel(
            tracking_number="OTH", gate_event=other_event
        )
        result = ParcelService.get_by_event(gate_event=self.gate_event)
        pks = [p.pk for p in result]
        self.assertIn(own.pk, pks)
        self.assertNotIn(other.pk, pks)

    def test_get_by_event_excludes_soft_deleted(self):
        parcel = self._make_parcel(tracking_number="DEL")
        parcel.is_active = False
        parcel.deleted_at = timezone.now()
        parcel.save(update_fields=["is_active", "deleted_at"])
        result = ParcelService.get_by_event(gate_event=self.gate_event)
        pks = [p.pk for p in result]
        self.assertNotIn(parcel.pk, pks)

    # --- get_overdue -----------------------------------------------------

    def test_get_overdue_returns_past_due_parcels(self):
        parcel = self._make_parcel(tracking_number="OVD")
        Parcel.objects.filter(pk=parcel.pk).update(
            stored_at=timezone.now() - timedelta(days=10)
        )
        result = ParcelService.get_overdue(society=self.society)
        pks = [p.pk for p in result]
        self.assertIn(parcel.pk, pks)

    def test_get_overdue_excludes_recent_parcels(self):
        parcel = self._make_parcel(tracking_number="REC")
        # stored_at is now() — well within the 7-day window.
        result = ParcelService.get_overdue(society=self.society)
        pks = [p.pk for p in result]
        self.assertNotIn(parcel.pk, pks)

    def test_get_overdue_excludes_non_received(self):
        parcel = self._make_parcel(tracking_number="COL")
        Parcel.objects.filter(pk=parcel.pk).update(
            stored_at=timezone.now() - timedelta(days=10)
        )
        ParcelService.collect_parcel(
            parcel=parcel,
            otp_code=parcel.otp_code,
            collected_by=self.user,
            actor=self.user,
        )
        result = ParcelService.get_overdue(society=self.society)
        pks = [p.pk for p in result]
        self.assertNotIn(parcel.pk, pks)

    def test_get_overdue_excludes_soft_deleted(self):
        parcel = self._make_parcel(tracking_number="DEL")
        Parcel.objects.filter(pk=parcel.pk).update(
            stored_at=timezone.now() - timedelta(days=10)
        )
        parcel.is_active = False
        parcel.deleted_at = timezone.now()
        parcel.save(update_fields=["is_active", "deleted_at"])
        result = ParcelService.get_overdue(society=self.society)
        pks = [p.pk for p in result]
        self.assertNotIn(parcel.pk, pks)

    def test_get_overdue_society_scoped(self):
        own = self._make_parcel(tracking_number="OWN")
        Parcel.objects.filter(pk=own.pk).update(
            stored_at=timezone.now() - timedelta(days=10)
        )
        other = self._make_other_parcel(tracking_number="OTH")
        Parcel.objects.filter(pk=other.pk).update(
            stored_at=timezone.now() - timedelta(days=10)
        )
        result = ParcelService.get_overdue(society=self.society)
        pks = [p.pk for p in result]
        self.assertIn(own.pk, pks)
        self.assertNotIn(other.pk, pks)

    def test_get_overdue_custom_max_storage_days(self):
        parcel = self._make_parcel(tracking_number="3D")
        # 3 days old — not overdue with default 7-day window, but overdue
        # with a 2-day window.
        Parcel.objects.filter(pk=parcel.pk).update(
            stored_at=timezone.now() - timedelta(days=3)
        )
        default_result = ParcelService.get_overdue(society=self.society)
        self.assertNotIn(parcel.pk, [p.pk for p in default_result])
        custom_result = ParcelService.get_overdue(
            society=self.society, max_storage_days=2
        )
        self.assertIn(parcel.pk, [p.pk for p in custom_result])

    def test_get_overdue_excludes_null_stored_at(self):
        # Create a parcel directly with stored_at=None (bypasses service
        # which always sets stored_at=now).
        parcel = Parcel.objects.create(
            society=self.society,
            gate_event=self.gate_event,
            tracking_number="NOSTORE",
            status=Parcel.Status.RECEIVED,
        )
        result = ParcelService.get_overdue(society=self.society)
        pks = [p.pk for p in result]
        self.assertNotIn(parcel.pk, pks)

    # --- generate_otp ----------------------------------------------------

    def test_generate_otp_returns_string(self):
        otp = ParcelService.generate_otp()
        self.assertIsInstance(otp, str)

    def test_generate_otp_default_length_six(self):
        otp = ParcelService.generate_otp()
        self.assertEqual(len(otp), 6)

    def test_generate_otp_custom_length(self):
        otp = ParcelService.generate_otp(length=8)
        self.assertEqual(len(otp), 8)

    def test_generate_otp_all_numeric(self):
        otp = ParcelService.generate_otp(length=10)
        self.assertTrue(otp.isdigit())

    def test_generate_otp_different_calls_produce_different_otp(self):
        # With 6 digits there are 10^6 possibilities; the chance of collision
        # in a small sample is negligible. Generate several and assert at
        # least two differ.
        codes = {ParcelService.generate_otp() for _ in range(20)}
        self.assertGreater(len(codes), 1)

    def test_generate_otp_rejects_zero_length(self):
        with self.assertRaises(ValueError):
            ParcelService.generate_otp(length=0)

    # --- _TRANSITIONS state machine -------------------------------------

    def test_transitions_from_received(self):
        allowed = ParcelService._TRANSITIONS[Parcel.Status.RECEIVED]
        self.assertIn(Parcel.Status.COLLECTED, allowed)
        self.assertIn(Parcel.Status.RETURNED, allowed)
        self.assertIn(Parcel.Status.LOST, allowed)

    def test_transitions_terminal_collected(self):
        allowed = ParcelService._TRANSITIONS[Parcel.Status.COLLECTED]
        self.assertEqual(allowed, set())

    def test_transitions_terminal_returned(self):
        allowed = ParcelService._TRANSITIONS[Parcel.Status.RETURNED]
        self.assertEqual(allowed, set())

    def test_transitions_terminal_lost(self):
        allowed = ParcelService._TRANSITIONS[Parcel.Status.LOST]
        self.assertEqual(allowed, set())


class ParcelViewTest(TestCase):
    """Frontend tests for the Phase 8 parcel management views.

    Societies are created once per class in setUpTestData; setUp logs in and
    selects the society so every view resolves the correct tenant.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = UserFactory(password="password")
        # create_society grants the user an active OWNER membership, which the
        # society-selection middleware requires to resolve the active society.
        cls.society = create_society(user=cls.user, name="Parcel View Society")
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
            user=UserFactory(password="password"), name="Other Parcel View Society"
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
        self.parcel = ParcelService.receive_parcel(
            gate_event=self.gate_event,
            tracking_number="VIEW-TRK-1",
            courier="DTDC",
            actor=self.user,
        )

    # --- helpers ---------------------------------------------------------

    def _select_society(self, society):
        session = self.client.session
        session[SESSION_SELECTED_SOCIETY_ID] = society.id
        session.save()

    def _make_other_parcel(self, **overrides):
        defaults = {
            "gate_event": self.other_gate_event,
            "tracking_number": "OTHER-TRK",
            "actor": self.user,
        }
        defaults.update(overrides)
        return ParcelService.receive_parcel(**defaults)

    # --- login required --------------------------------------------------

    def test_list_view_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("gateops:parcel-list"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.url)

    def test_receive_view_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("gateops:parcel-receive"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.url)

    def test_pending_view_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("gateops:parcel-pending"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.url)

    def test_overdue_view_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("gateops:parcel-overdue"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.url)

    def test_detail_view_requires_login(self):
        self.client.logout()
        response = self.client.get(
            reverse("gateops:parcel-detail", kwargs={"pk": self.parcel.pk})
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.url)

    # --- list / pending / overdue views ---------------------------------

    def test_list_view_returns_200(self):
        response = self.client.get(reverse("gateops:parcel-list"))
        self.assertEqual(response.status_code, 200)

    def test_pending_view_returns_200(self):
        response = self.client.get(reverse("gateops:parcel-pending"))
        self.assertEqual(response.status_code, 200)

    def test_overdue_view_returns_200(self):
        response = self.client.get(reverse("gateops:parcel-overdue"))
        self.assertEqual(response.status_code, 200)

    # --- receive view ----------------------------------------------------

    def test_receive_view_get_returns_200(self):
        response = self.client.get(reverse("gateops:parcel-receive"))
        self.assertEqual(response.status_code, 200)

    def test_receive_view_post_creates_parcel(self):
        response = self.client.post(
            reverse("gateops:parcel-receive"),
            data={
                "gate_event_id": self.gate_event.pk,
                "tracking_number": "POST-TRK-1",
                "courier": "BlueDart",
            },
        )
        self.assertEqual(response.status_code, 302)
        parcel = Parcel.objects.get(
            society=self.society,
            gate_event=self.gate_event,
            tracking_number="POST-TRK-1",
        )
        self.assertEqual(parcel.courier, "BlueDart")
        self.assertEqual(parcel.status, Parcel.Status.RECEIVED)
        self.assertEqual(
            response.url,
            reverse("gateops:parcel-detail", kwargs={"pk": parcel.pk}),
        )

    # --- detail view ----------------------------------------------------

    def test_detail_view_200_for_own_society(self):
        response = self.client.get(
            reverse("gateops:parcel-detail", kwargs={"pk": self.parcel.pk})
        )
        self.assertEqual(response.status_code, 200)

    def test_detail_view_404_for_other_society(self):
        other = self._make_other_parcel()
        response = self.client.get(
            reverse("gateops:parcel-detail", kwargs={"pk": other.pk})
        )
        self.assertEqual(response.status_code, 404)

    # --- collect view ---------------------------------------------------

    def test_collect_view_post_only(self):
        response = self.client.get(
            reverse("gateops:parcel-collect", kwargs={"pk": self.parcel.pk})
        )
        self.assertEqual(response.status_code, 405)

    def test_collect_view_post_transitions_to_collected(self):
        response = self.client.post(
            reverse("gateops:parcel-collect", kwargs={"pk": self.parcel.pk}),
            data={"otp_code": self.parcel.otp_code},
        )
        self.assertEqual(response.status_code, 302)
        self.parcel.refresh_from_db()
        self.assertEqual(self.parcel.status, Parcel.Status.COLLECTED)
        self.assertIsNotNone(self.parcel.collected_at)

    def test_collect_view_404_for_other_society(self):
        other = self._make_other_parcel()
        response = self.client.post(
            reverse("gateops:parcel-collect", kwargs={"pk": other.pk}),
            data={"otp_code": other.otp_code},
        )
        self.assertEqual(response.status_code, 404)

    # --- return view ----------------------------------------------------

    def test_return_view_post_only(self):
        response = self.client.get(
            reverse("gateops:parcel-return", kwargs={"pk": self.parcel.pk})
        )
        self.assertEqual(response.status_code, 405)

    def test_return_view_post_transitions_to_returned(self):
        response = self.client.post(
            reverse("gateops:parcel-return", kwargs={"pk": self.parcel.pk}),
            data={"reason": "Refused"},
        )
        self.assertEqual(response.status_code, 302)
        self.parcel.refresh_from_db()
        self.assertEqual(self.parcel.status, Parcel.Status.RETURNED)

    def test_return_view_404_for_other_society(self):
        other = self._make_other_parcel()
        response = self.client.post(
            reverse("gateops:parcel-return", kwargs={"pk": other.pk})
        )
        self.assertEqual(response.status_code, 404)

    # --- mark lost view -------------------------------------------------

    def test_mark_lost_view_post_only(self):
        response = self.client.get(
            reverse("gateops:parcel-mark-lost", kwargs={"pk": self.parcel.pk})
        )
        self.assertEqual(response.status_code, 405)

    def test_mark_lost_view_post_transitions_to_lost(self):
        response = self.client.post(
            reverse("gateops:parcel-mark-lost", kwargs={"pk": self.parcel.pk})
        )
        self.assertEqual(response.status_code, 302)
        self.parcel.refresh_from_db()
        self.assertEqual(self.parcel.status, Parcel.Status.LOST)

    def test_mark_lost_view_404_for_other_society(self):
        other = self._make_other_parcel()
        response = self.client.post(
            reverse("gateops:parcel-mark-lost", kwargs={"pk": other.pk})
        )
        self.assertEqual(response.status_code, 404)
