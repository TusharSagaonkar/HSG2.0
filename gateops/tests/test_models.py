"""Tests for the Phase-1 gateops foundation models.

Covers, per model: creation with valid data, society FK requirement, ``clean()``
validation rules, conditional ``UniqueConstraint`` (active vs soft-deleted),
soft-delete behaviour, and society isolation.
"""

from datetime import date, time, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from gateops.models import (
    ApprovalType,
    Gate,
    GateOpsAuditLog,
    GateOpsRole,
    GateOpsSocietyConfig,
    GuardShift,
    GuardShiftAssignment,
    HolidayCalendar,
    MasterSettings,
    MaterialCategory,
    NotificationPreference,
    PassType,
    SecurityGuard,
    VehicleCategory,
    VisitorCategory,
)
from societies.models import Society


class GateOpsModelTestBase(TestCase):
    """Shared helpers for gateops model tests.

    Societies are created once per class via ``setUpTestData`` to avoid
    re-running the expensive accounting + gateops bootstrap signal on every
    test method. Per-test mutable records (guards, shifts, gates) are still
    created in ``setUp``.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # Creating a Society triggers the gateops bootstrap signal, which
        # seeds default categories/roles/etc. Tests that need a clean slate
        # can filter to records they create themselves.
        cls.society = Society.objects.create(name="Alpha Society")
        cls.other_society = Society.objects.create(name="Beta Society")

    # --- helpers ----------------------------------------------------------

    def _make_gate(self, society=None, code="MAIN", name="Main Gate"):
        return Gate.objects.create(
            society=society or self.society,
            name=name,
            code=code,
            gate_type=Gate.GateType.MAIN,
        )

    def _make_guard(self, society=None, name="Ramesh", badge="B001"):
        return SecurityGuard.objects.create(
            society=society or self.society,
            name=name,
            badge_number=badge,
        )

    def _make_shift(self, society=None, name="Morning"):
        return GuardShift.objects.create(
            society=society or self.society,
            name=name,
            start_time=time(6, 0),
            end_time=time(14, 0),
        )


# ---------------------------------------------------------------------------
# GateOpsSocietyConfig
# ---------------------------------------------------------------------------


class GateOpsSocietyConfigTest(GateOpsModelTestBase):
    def test_creation_with_defaults(self):
        # Bootstrap already created one for self.society; verify it exists.
        cfg = GateOpsSocietyConfig.objects.get(society=self.society)
        self.assertEqual(cfg.offline_sync_window_hours, 24)
        self.assertEqual(cfg.default_approval_timeout_minutes, 15)
        self.assertTrue(cfg.photo_required)

    def test_society_one_to_one(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                GateOpsSocietyConfig.objects.create(society=self.society)

    def test_clean_otp_length_out_of_range(self):
        cfg = GateOpsSocietyConfig.objects.get(society=self.society)
        cfg.otp_length = 3
        with self.assertRaises(ValidationError):
            cfg.clean()

    def test_clean_night_mode_partial(self):
        cfg = GateOpsSocietyConfig.objects.get(society=self.society)
        cfg.night_mode_start = time(22, 0)
        cfg.night_mode_end = None
        with self.assertRaises(ValidationError):
            cfg.clean()


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------


class GateTest(GateOpsModelTestBase):
    def test_creation(self):
        gate = self._make_gate(code="SERV", name="Service Gate")
        self.assertEqual(gate.gate_type, Gate.GateType.MAIN)
        self.assertTrue(gate.is_active)

    def test_code_uppercase_enforcement(self):
        # Lowercase code is normalized to uppercase in clean().
        gate = Gate(society=self.society, name="Side", code="side")
        gate.clean()
        self.assertEqual(gate.code, "SIDE")

    def test_clean_requires_code(self):
        gate = Gate(society=self.society, name="X", code="")
        with self.assertRaises(ValidationError):
            gate.clean()

    def test_unique_code_per_society_active(self):
        self._make_gate(code="UNIQ", name="A")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self._make_gate(code="UNIQ", name="B")

    def test_soft_deleted_code_can_coexist(self):
        """An inactive (soft-deleted) gate does not block reusing its code."""
        old = self._make_gate(code="REUSE", name="Old")
        old.is_active = False
        old.deleted_at = timezone.now()
        old.save()
        # New active gate with same code should succeed.
        new = self._make_gate(code="REUSE", name="New")
        self.assertTrue(new.is_active)

    def test_society_isolation(self):
        gate_a = self._make_gate(code="ISO", name="A")
        gate_b = self._make_gate(society=self.other_society, code="ISO", name="B")
        self.assertEqual(
            set(Gate.objects.filter(society=self.society).values_list("code", flat=True)),
            # Includes the bootstrap "MAIN" gate plus "ISO".
            {"MAIN", "ISO"},
        )
        self.assertIn(gate_b, Gate.objects.filter(society=self.other_society))


# ---------------------------------------------------------------------------
# SecurityGuard
# ---------------------------------------------------------------------------


class SecurityGuardTest(GateOpsModelTestBase):
    def test_creation(self):
        guard = self._make_guard()
        self.assertTrue(guard.is_active)
        self.assertEqual(guard.badge_number, "B001")

    def test_clean_requires_user_or_name(self):
        guard = SecurityGuard(society=self.society, name="", badge_number="X")
        with self.assertRaises(ValidationError):
            guard.clean()

    def test_clean_phone_digits(self):
        guard = SecurityGuard(society=self.society, name="G", phone="123")
        with self.assertRaises(ValidationError):
            guard.clean()

    def test_unique_badge_active(self):
        self._make_guard(badge="DUP")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self._make_guard(badge="DUP")

    def test_soft_delete(self):
        guard = self._make_guard()
        guard.is_active = False
        guard.deleted_at = timezone.now()
        guard.save()
        guard.refresh_from_db()
        self.assertFalse(guard.is_active)
        self.assertIsNotNone(guard.deleted_at)


# ---------------------------------------------------------------------------
# GuardShift
# ---------------------------------------------------------------------------


class GuardShiftTest(GateOpsModelTestBase):
    def test_creation(self):
        shift = self._make_shift()
        self.assertEqual(shift.start_time, time(6, 0))

    def test_clean_end_before_start(self):
        shift = GuardShift(
            society=self.society,
            name="Bad",
            start_time=time(10, 0),
            end_time=time(8, 0),
        )
        with self.assertRaises(ValidationError):
            shift.clean()

    def test_soft_delete(self):
        shift = self._make_shift()
        shift.is_active = False
        shift.deleted_at = timezone.now()
        shift.save()
        shift.refresh_from_db()
        self.assertFalse(shift.is_active)


# ---------------------------------------------------------------------------
# GuardShiftAssignment
# ---------------------------------------------------------------------------


class GuardShiftAssignmentTest(GateOpsModelTestBase):
    def setUp(self):
        super().setUp()
        self.guard = self._make_guard()
        self.shift = self._make_shift()
        self.gate = self._make_gate(code="G1", name="Gate 1")

    def test_creation(self):
        assignment = GuardShiftAssignment.objects.create(
            society=self.society,
            guard=self.guard,
            shift=self.shift,
            gate=self.gate,
            date=date(2026, 6, 28),
        )
        self.assertEqual(GuardShiftAssignment.objects.count(), 1)

    def test_clean_check_out_before_check_in(self):
        assignment = GuardShiftAssignment(
            society=self.society,
            guard=self.guard,
            shift=self.shift,
            gate=self.gate,
            date=date(2026, 6, 28),
            check_in_at=timezone.now(),
            check_out_at=timezone.now() - timedelta(hours=1),
        )
        with self.assertRaises(ValidationError):
            assignment.clean()

    def test_unique_guard_shift_per_day(self):
        GuardShiftAssignment.objects.create(
            society=self.society,
            guard=self.guard,
            shift=self.shift,
            gate=self.gate,
            date=date(2026, 6, 28),
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                GuardShiftAssignment.objects.create(
                    society=self.society,
                    guard=self.guard,
                    shift=self.shift,
                    gate=self.gate,
                    date=date(2026, 6, 28),
                )


# ---------------------------------------------------------------------------
# VisitorCategory
# ---------------------------------------------------------------------------


class VisitorCategoryTest(GateOpsModelTestBase):
    def test_creation(self):
        cat = VisitorCategory.objects.create(
            society=self.society, name="Guest", code="GUESTX"
        )
        self.assertFalse(cat.requires_approval_default)

    def test_clean_code_uppercase(self):
        cat = VisitorCategory(society=self.society, name="X", code="lower")
        with self.assertRaises(ValidationError):
            cat.clean()

    def test_unique_code_active(self):
        VisitorCategory.objects.create(society=self.society, name="A", code="VC1")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                VisitorCategory.objects.create(society=self.society, name="B", code="VC1")

    def test_inactive_code_can_coexist(self):
        old = VisitorCategory.objects.create(society=self.society, name="A", code="COEX")
        old.is_active = False
        old.deleted_at = timezone.now()
        old.save()
        new = VisitorCategory.objects.create(society=self.society, name="B", code="COEX")
        self.assertTrue(new.is_active)

    def test_society_isolation(self):
        VisitorCategory.objects.create(society=self.society, name="A", code="ISO")
        VisitorCategory.objects.create(society=self.other_society, name="B", code="ISO")
        self.assertEqual(
            VisitorCategory.objects.filter(society=self.society, code="ISO").count(), 1
        )


# ---------------------------------------------------------------------------
# VehicleCategory
# ---------------------------------------------------------------------------


class VehicleCategoryTest(GateOpsModelTestBase):
    def test_creation(self):
        cat = VehicleCategory.objects.create(
            society=self.society, name="Car", code="CARX"
        )
        self.assertFalse(cat.is_commercial)

    def test_clean_code_uppercase(self):
        cat = VehicleCategory(society=self.society, name="X", code="lower")
        with self.assertRaises(ValidationError):
            cat.clean()

    def test_unique_code_active(self):
        VehicleCategory.objects.create(society=self.society, name="A", code="VC1")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                VehicleCategory.objects.create(society=self.society, name="B", code="VC1")


# ---------------------------------------------------------------------------
# MaterialCategory
# ---------------------------------------------------------------------------


class MaterialCategoryTest(GateOpsModelTestBase):
    def test_creation(self):
        cat = MaterialCategory.objects.create(
            society=self.society, name="Cement", code="CEMX"
        )
        self.assertTrue(cat.is_inbound_default)

    def test_clean_code_uppercase(self):
        cat = MaterialCategory(society=self.society, name="X", code="lower")
        with self.assertRaises(ValidationError):
            cat.clean()

    def test_unique_code_active(self):
        MaterialCategory.objects.create(society=self.society, name="A", code="MC1")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                MaterialCategory.objects.create(society=self.society, name="B", code="MC1")


# ---------------------------------------------------------------------------
# PassType
# ---------------------------------------------------------------------------


class PassTypeTest(GateOpsModelTestBase):
    def test_creation(self):
        pt = PassType.objects.create(
            society=self.society,
            name="QR",
            code="QRX",
            validation_method=PassType.ValidationMethod.QR,
            duration_type=PassType.DurationType.ONE_TIME,
        )
        self.assertEqual(pt.default_validity_hours, 24)

    def test_clean_code_uppercase(self):
        pt = PassType(
            society=self.society,
            name="X",
            code="lower",
            validation_method=PassType.ValidationMethod.QR,
            duration_type=PassType.DurationType.ONE_TIME,
        )
        with self.assertRaises(ValidationError):
            pt.clean()

    def test_unique_code_active(self):
        PassType.objects.create(
            society=self.society,
            name="A",
            code="PT1",
            validation_method=PassType.ValidationMethod.QR,
            duration_type=PassType.DurationType.ONE_TIME,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                PassType.objects.create(
                    society=self.society,
                    name="B",
                    code="PT1",
                    validation_method=PassType.ValidationMethod.QR,
                    duration_type=PassType.DurationType.ONE_TIME,
                )


# ---------------------------------------------------------------------------
# ApprovalType
# ---------------------------------------------------------------------------


class ApprovalTypeTest(GateOpsModelTestBase):
    def test_creation(self):
        at = ApprovalType.objects.create(
            society=self.society,
            name="Auto",
            code="ATX",
            approver=ApprovalType.Approver.AUTO,
        )
        self.assertEqual(at.escalation_timeout_minutes, 15)

    def test_clean_code_uppercase(self):
        at = ApprovalType(
            society=self.society,
            name="X",
            code="lower",
            approver=ApprovalType.Approver.AUTO,
        )
        with self.assertRaises(ValidationError):
            at.clean()

    def test_unique_code_active(self):
        ApprovalType.objects.create(
            society=self.society,
            name="A",
            code="AP1",
            approver=ApprovalType.Approver.AUTO,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ApprovalType.objects.create(
                    society=self.society,
                    name="B",
                    code="AP1",
                    approver=ApprovalType.Approver.AUTO,
                )


# ---------------------------------------------------------------------------
# NotificationPreference
# ---------------------------------------------------------------------------


class NotificationPreferenceTest(GateOpsModelTestBase):
    def setUp(self):
        super().setUp()
        self.vcat = VisitorCategory.objects.create(
            society=self.society, name="Guest", code="NP1"
        )

    def test_creation(self):
        pref = NotificationPreference.objects.create(
            society=self.society,
            visitor_category=self.vcat,
            channel=NotificationPreference.Channel.PUSH,
            trigger=NotificationPreference.Trigger.ARRIVAL,
        )
        self.assertEqual(pref.channel, "push")

    def test_multiple_channels_per_category(self):
        """The unique constraint includes ``channel`` so multiple channels per
        visitor category are allowed."""
        NotificationPreference.objects.create(
            society=self.society,
            visitor_category=self.vcat,
            channel=NotificationPreference.Channel.PUSH,
        )
        NotificationPreference.objects.create(
            society=self.society,
            visitor_category=self.vcat,
            channel=NotificationPreference.Channel.SMS,
        )
        self.assertEqual(
            NotificationPreference.objects.filter(visitor_category=self.vcat).count(), 2
        )

    def test_duplicate_channel_blocked(self):
        NotificationPreference.objects.create(
            society=self.society,
            visitor_category=self.vcat,
            channel=NotificationPreference.Channel.PUSH,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                NotificationPreference.objects.create(
                    society=self.society,
                    visitor_category=self.vcat,
                    channel=NotificationPreference.Channel.PUSH,
                )


# ---------------------------------------------------------------------------
# GateOpsRole
# ---------------------------------------------------------------------------


class GateOpsRoleTest(GateOpsModelTestBase):
    def test_creation(self):
        # Bootstrap already created GATE_ADMIN; fetch it and verify has_perm.
        role = GateOpsRole.objects.get(
            society=self.society,
            code=GateOpsRole.RoleCode.GATE_ADMIN,
        )
        self.assertTrue(role.has_perm("can_create_event"))

    def test_clean_unknown_permission_key(self):
        role = GateOpsRole(
            society=self.society,
            name="X",
            code=GateOpsRole.RoleCode.GUARD,
            permissions={"bogus_perm": True},
        )
        with self.assertRaises(ValidationError):
            role.clean()

    def test_clean_non_boolean_value(self):
        role = GateOpsRole(
            society=self.society,
            name="X",
            code=GateOpsRole.RoleCode.GUARD,
            permissions={"can_create_event": "yes"},
        )
        with self.assertRaises(ValidationError):
            role.clean()

    def test_unique_code_active(self):
        # Bootstrap already created GUARD for this society; attempting to
        # create another active role with the same code must fail.
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                GateOpsRole.objects.create(
                    society=self.society,
                    name="Duplicate Guard",
                    code=GateOpsRole.RoleCode.GUARD,
                    permissions={},
                )


# ---------------------------------------------------------------------------
# GateOpsAuditLog
# ---------------------------------------------------------------------------


class GateOpsAuditLogModelTest(GateOpsModelTestBase):
    def test_creation_via_log(self):
        entry = GateOpsAuditLog.log(
            society=self.society,
            action=GateOpsAuditLog.Action.CREATE,
            entity_type="GateEvent",
            entity_id=42,
            before_value={"status": "invited"},
            after_value={"status": "arrived"},
            device_info={"user_agent": "GuardApp/1.0"},
        )
        self.assertEqual(entry.entity_id, "42")
        self.assertEqual(entry.device_info, {"user_agent": "GuardApp/1.0"})

    def test_update_rejected(self):
        entry = GateOpsAuditLog.log(
            society=self.society,
            action=GateOpsAuditLog.Action.CREATE,
            entity_type="GateEvent",
            entity_id=1,
        )
        with self.assertRaises(PermissionError):
            entry.save()

    def test_delete_rejected(self):
        entry = GateOpsAuditLog.log(
            society=self.society,
            action=GateOpsAuditLog.Action.CREATE,
            entity_type="GateEvent",
            entity_id=1,
        )
        with self.assertRaises(PermissionError):
            entry.delete()

    def test_device_info_accepts_dict(self):
        entry = GateOpsAuditLog.log(
            society=self.society,
            action=GateOpsAuditLog.Action.UPDATE,
            entity_type="Person",
            entity_id=7,
            device_info={"app_version": "2.1", "device_id": "abc"},
        )
        entry.refresh_from_db()
        self.assertEqual(entry.device_info["device_id"], "abc")

    def test_rule_applied_nullable(self):
        # Phase 2 not implemented; rule_applied FK is omitted in Phase 1, so
        # there is no rule linkage. Verify the log entry still persists.
        entry = GateOpsAuditLog.log(
            society=self.society,
            action=GateOpsAuditLog.Action.RULE_EVALUATED,
            entity_type="GateEvent",
            entity_id=99,
        )
        self.assertIsNotNone(entry.pk)
        self.assertFalse(hasattr(entry, "rule_applied"))


# ---------------------------------------------------------------------------
# HolidayCalendar
# ---------------------------------------------------------------------------


class HolidayCalendarTest(GateOpsModelTestBase):
    def test_creation(self):
        hol = HolidayCalendar.objects.create(
            society=self.society, name="Independence Day", date=date(2026, 8, 15)
        )
        self.assertEqual(hol.affects, HolidayCalendar.Affects.ALL)

    def test_unique_date_per_society(self):
        HolidayCalendar.objects.create(
            society=self.society, name="A", date=date(2026, 1, 1)
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                HolidayCalendar.objects.create(
                    society=self.society, name="B", date=date(2026, 1, 1)
                )

    def test_same_date_different_societies(self):
        HolidayCalendar.objects.create(
            society=self.society, name="A", date=date(2026, 1, 1)
        )
        HolidayCalendar.objects.create(
            society=self.other_society, name="B", date=date(2026, 1, 1)
        )
        self.assertEqual(HolidayCalendar.objects.filter(society=self.society).count(), 1)


# ---------------------------------------------------------------------------
# MasterSettings
# ---------------------------------------------------------------------------


class MasterSettingsTest(GateOpsModelTestBase):
    def test_creation(self):
        ms = MasterSettings.objects.get(society=self.society)
        self.assertEqual(ms.settings, {})

    def test_one_to_one(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                MasterSettings.objects.create(society=self.society)

    def test_settings_json_roundtrip(self):
        ms = MasterSettings.objects.get(society=self.society)
        ms.settings = {"default_language": "en", "enable_face_match": False}
        ms.save()
        ms.refresh_from_db()
        self.assertEqual(ms.settings["default_language"], "en")
