"""Comprehensive tests for SocietySetupService, ModuleConfigurationService,
and FinancialYearSetupService (Phase 9).

These tests complement the smoke tests in ``test_services_setup_smoke.py``
by covering edge cases, error paths, idempotency, and audit-trail integrity.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from accounting.models import AccountingPeriod, FinancialYear
from members.models import Member, Structure, Unit
from onboarding.models import MigrationAuditLog, OnboardingWizard
from onboarding.services import (
    FinancialYearSetupService,
    ModuleConfigurationService,
    SocietySetupService,
    WizardService,
)
from onboarding.services.financial_year_service import (
    FY_PATTERN_APRIL_MARCH,
    FY_PATTERN_JAN_DEC,
    FY_PATTERN_JUL_JUN,
)

User = get_user_model()


def _make_society_data(name="Test Society Setup", **overrides):
    """Helper to build a society_data dict for create_society."""
    data = {
        "name": name,
        "registration_number": "REG-SETUP-001",
        "address": "123 Test Street",
        "city": "Mumbai",
        "state": "Maharashtra",
        "country": "India",
        "pin_code": "400001",
        "pan": "ABCDE1234F",
        "email": "society@example.com",
        "phone": "9876543210",
        "financial_year_pattern": "APRIL_MARCH",
    }
    data.update(overrides)
    return data


class SocietySetupCreateSocietyTest(TestCase):
    """Tests for SocietySetupService.create_society (Step 1)."""

    def setUp(self):
        self.user = User.objects.create(
            email="socsetup@example.com", name="Soc Setup", is_active=True
        )
        self.wizard = WizardService.create_wizard(user=self.user)

    def test_create_society_missing_name_raises(self):
        with self.assertRaises(ValidationError):
            SocietySetupService.create_society(
                self.wizard, {"name": "", "address": "Test"}, user=self.user
            )

    def test_create_society_empty_data_raises(self):
        with self.assertRaises(ValidationError):
            SocietySetupService.create_society(self.wizard, {}, user=self.user)

    def test_create_society_none_data_raises(self):
        with self.assertRaises(ValidationError):
            SocietySetupService.create_society(self.wizard, None, user=self.user)

    def test_create_society_links_wizard(self):
        society = SocietySetupService.create_society(
            self.wizard, _make_society_data(), user=self.user
        )
        self.wizard.refresh_from_db()
        self.assertEqual(self.wizard.society_id, society.id)

    def test_create_society_stores_extra_fields(self):
        society = SocietySetupService.create_society(
            self.wizard,
            _make_society_data(city="Pune", state="Maharashtra", pan="XYZAB9999C"),
            user=self.user,
        )
        self.wizard.refresh_from_db()
        self.assertEqual(self.wizard.wizard_data.get("city"), "Pune")
        self.assertEqual(self.wizard.wizard_data.get("pan"), "XYZAB9999C")
        self.assertEqual(
            self.wizard.wizard_data.get("financial_year_pattern"), "APRIL_MARCH"
        )

    def test_create_society_creates_audit_log(self):
        SocietySetupService.create_society(
            self.wizard, _make_society_data(), user=self.user
        )
        self.assertTrue(
            MigrationAuditLog.objects.filter(
                wizard=self.wizard, action="CREATE_SOCIETY"
            ).exists()
        )

    def test_create_society_defaults_user_to_created_by(self):
        """If user is None, the wizard's created_by is used."""
        society = SocietySetupService.create_society(
            self.wizard, _make_society_data()
        )
        self.assertIsNotNone(society)


class SocietySetupCreateStructureTest(TestCase):
    """Tests for SocietySetupService.create_structure (Step 6)."""

    def setUp(self):
        self.user = User.objects.create(
            email="struct@example.com", name="Struct User", is_active=True
        )
        self.wizard = WizardService.create_wizard(user=self.user)
        self.society = SocietySetupService.create_society(
            self.wizard, _make_society_data("Struct Society"), user=self.user
        )

    def test_create_structure_empty_raises(self):
        with self.assertRaises(ValidationError):
            SocietySetupService.create_structure(
                self.wizard, [], user=self.user
            )

    def test_create_structure_no_society_raises(self):
        wizard = WizardService.create_wizard(user=self.user)
        with self.assertRaises(ValidationError):
            SocietySetupService.create_structure(
                wizard, [{"building_name": "B1"}], user=self.user
            )

    def test_create_structure_idempotent(self):
        """Calling create_structure twice with same data should not duplicate."""
        data = [{"building_name": "Tower A", "wing_name": "Wing 1", "floor_number": 1}]
        SocietySetupService.create_structure(self.wizard, data, user=self.user)
        SocietySetupService.create_structure(self.wizard, data, user=self.user)
        count = Structure.objects.filter(
            society=self.society, name="Tower A", parent__isnull=True
        ).count()
        self.assertEqual(count, 1)

    def test_create_structure_building_only(self):
        SocietySetupService.create_structure(
            self.wizard, [{"building_name": "Solo Tower"}], user=self.user
        )
        building = Structure.objects.filter(
            society=self.society, name="Solo Tower", parent__isnull=True
        ).first()
        self.assertIsNotNone(building)
        self.assertEqual(
            building.structure_type, Structure.StructureType.BUILDING
        )

    def test_create_structure_full_hierarchy(self):
        SocietySetupService.create_structure(
            self.wizard,
            [{"building_name": "B1", "wing_name": "W1", "floor_number": 3}],
            user=self.user,
        )
        building = Structure.objects.filter(
            society=self.society, name="B1", parent__isnull=True
        ).first()
        wing = Structure.objects.filter(
            society=self.society, name="W1", parent=building
        ).first()
        floor = Structure.objects.filter(
            society=self.society, name="3", parent=wing
        ).first()
        self.assertIsNotNone(wing)
        self.assertIsNotNone(floor)
        self.assertEqual(wing.structure_type, Structure.StructureType.WING)
        self.assertEqual(floor.structure_type, Structure.StructureType.FLOOR)

    def test_create_structure_creates_audit_log(self):
        SocietySetupService.create_structure(
            self.wizard, [{"building_name": "Audit B"}], user=self.user
        )
        self.assertTrue(
            MigrationAuditLog.objects.filter(
                wizard=self.wizard, action="CREATE_STRUCTURES"
            ).exists()
        )


class SocietySetupCreateUnitsTest(TestCase):
    """Tests for SocietySetupService.create_units (Step 7)."""

    def setUp(self):
        self.user = User.objects.create(
            email="units@example.com", name="Units User", is_active=True
        )
        self.wizard = WizardService.create_wizard(user=self.user)
        SocietySetupService.create_society(
            self.wizard, _make_society_data("Units Society"), user=self.user
        )
        SocietySetupService.create_structure(
            self.wizard,
            [{"building_name": "B1", "floor_number": 1}],
            user=self.user,
        )

    def test_create_units_idempotent(self):
        data = [
            {"flat_number": "101", "area": "850.00", "usage_type": "RESIDENTIAL",
             "building": "B1", "floor": 1}
        ]
        SocietySetupService.create_units(self.wizard, data, user=self.user)
        SocietySetupService.create_units(self.wizard, data, user=self.user)
        count = Unit.objects.filter(identifier="101").count()
        self.assertEqual(count, 1)

    def test_create_units_commercial_maps_to_office(self):
        data = [
            {"flat_number": "201", "area": "1200.00", "usage_type": "COMMERCIAL",
             "building": "B1", "floor": 1}
        ]
        SocietySetupService.create_units(self.wizard, data, user=self.user)
        unit = Unit.objects.get(identifier="201")
        self.assertEqual(unit.unit_type, Unit.UnitType.OFFICE)

    def test_create_units_residential_maps_to_flat(self):
        data = [
            {"flat_number": "301", "area": "700.00", "usage_type": "RESIDENTIAL",
             "building": "B1", "floor": 1}
        ]
        SocietySetupService.create_units(self.wizard, data, user=self.user)
        unit = Unit.objects.get(identifier="301")
        self.assertEqual(unit.unit_type, Unit.UnitType.FLAT)

    def test_create_units_decimal_area(self):
        data = [
            {"flat_number": "401", "area": "950.50", "usage_type": "RESIDENTIAL",
             "building": "B1", "floor": 1}
        ]
        SocietySetupService.create_units(self.wizard, data, user=self.user)
        unit = Unit.objects.get(identifier="401")
        self.assertEqual(unit.area_sqft, Decimal("950.50"))

    def test_create_units_creates_audit_log(self):
        data = [
            {"flat_number": "501", "area": "800", "usage_type": "RESIDENTIAL",
             "building": "B1", "floor": 1}
        ]
        SocietySetupService.create_units(self.wizard, data, user=self.user)
        self.assertTrue(
            MigrationAuditLog.objects.filter(
                wizard=self.wizard, action="CREATE_UNITS"
            ).exists()
        )


class SocietySetupAssignMembersTest(TestCase):
    """Tests for SocietySetupService.assign_members (Step 8)."""

    def setUp(self):
        self.user = User.objects.create(
            email="members@example.com", name="Members User", is_active=True
        )
        self.wizard = WizardService.create_wizard(user=self.user)
        SocietySetupService.create_society(
            self.wizard, _make_society_data("Members Society"), user=self.user
        )
        SocietySetupService.create_structure(
            self.wizard,
            [{"building_name": "B1", "floor_number": 1}],
            user=self.user,
        )
        SocietySetupService.create_units(
            self.wizard,
            [{"flat_number": "101", "building": "B1", "floor": 1}],
            user=self.user,
        )

    def test_assign_members_owner_role(self):
        members = SocietySetupService.assign_members(
            self.wizard,
            [{
                "member_name": "Alice Owner",
                "member_type": "OWNER",
                "unit_identifier": "101",
                "email": "alice@example.com",
                "phone": "9876543210",
            }],
            user=self.user,
        )
        self.assertEqual(len(members), 1)
        self.assertEqual(members[0].role, Member.MemberRole.OWNER)

    def test_assign_members_tenant_role(self):
        members = SocietySetupService.assign_members(
            self.wizard,
            [{
                "member_name": "Bob Tenant",
                "member_type": "TENANT",
                "unit_identifier": "101",
                "email": "bob@example.com",
                "phone": "9876543211",
            }],
            user=self.user,
        )
        self.assertEqual(members[0].role, Member.MemberRole.TENANT)

    def test_assign_members_creates_audit_log(self):
        SocietySetupService.assign_members(
            self.wizard,
            [{
                "member_name": "Carol",
                "member_type": "OWNER",
                "unit_identifier": "101",
                "email": "carol@example.com",
                "phone": "9876543212",
            }],
            user=self.user,
        )
        self.assertTrue(
            MigrationAuditLog.objects.filter(
                wizard=self.wizard, action="ASSIGN_MEMBERS"
            ).exists()
        )


class ModuleConfigurationServiceTest(TestCase):
    """Tests for ModuleConfigurationService (Step 3)."""

    def setUp(self):
        self.user = User.objects.create(
            email="modcfg@example.com", name="Mod Cfg", is_active=True
        )
        self.wizard = WizardService.create_wizard(user=self.user)

    def test_configure_modules_filters_unknown(self):
        enabled = ModuleConfigurationService.configure_modules(
            self.wizard, ["parking", "unknown_mod", "shares"], user=self.user
        )
        self.assertIn("parking", enabled)
        self.assertIn("shares", enabled)
        self.assertNotIn("unknown_mod", enabled)

    def test_configure_modules_normalizes_case(self):
        enabled = ModuleConfigurationService.configure_modules(
            self.wizard, ["PARKING", "Shares"], user=self.user
        )
        self.assertIn("parking", enabled)
        self.assertIn("shares", enabled)

    def test_configure_modules_strips_whitespace(self):
        enabled = ModuleConfigurationService.configure_modules(
            self.wizard, ["  parking  "], user=self.user
        )
        self.assertIn("parking", enabled)

    def test_configure_modules_persists_to_wizard(self):
        ModuleConfigurationService.configure_modules(
            self.wizard, ["parking", "gateops"], user=self.user
        )
        self.wizard.refresh_from_db()
        self.assertIn("parking", self.wizard.selected_modules)
        self.assertIn("gateops", self.wizard.selected_modules)

    def test_configure_modules_creates_audit_log(self):
        ModuleConfigurationService.configure_modules(
            self.wizard, ["parking"], user=self.user
        )
        self.assertTrue(
            MigrationAuditLog.objects.filter(
                wizard=self.wizard, action="CONFIGURE_MODULES"
            ).exists()
        )

    def test_get_enabled_modules_merges_core(self):
        """get_enabled_modules always includes core even if not stored."""
        self.wizard.selected_modules = ["parking"]
        self.wizard.save(update_fields=["selected_modules"])
        enabled = ModuleConfigurationService.get_enabled_modules(self.wizard)
        self.assertIn("accounting", enabled)
        self.assertIn("billing", enabled)
        self.assertIn("members", enabled)
        self.assertIn("administration", enabled)
        self.assertIn("parking", enabled)

    def test_get_module_display_names_returns_copy(self):
        names1 = ModuleConfigurationService.get_module_display_names()
        names1["test_key"] = "test"
        names2 = ModuleConfigurationService.get_module_display_names()
        self.assertNotIn("test_key", names2)

    def test_get_module_display_names_includes_all_modules(self):
        names = ModuleConfigurationService.get_module_display_names()
        self.assertIn("accounting", names)
        self.assertIn("parking", names)
        self.assertIn("gateops", names)
        self.assertIn("shares", names)


class FinancialYearSetupServiceTest(TestCase):
    """Tests for FinancialYearSetupService (Steps 4 & 5)."""

    def setUp(self):
        self.user = User.objects.create(
            email="fysetup@example.com", name="FY Setup", is_active=True
        )
        self.wizard = WizardService.create_wizard(user=self.user)
        self.society = SocietySetupService.create_society(
            self.wizard, _make_society_data("FY Setup Society"), user=self.user
        )

    def test_create_financial_year_no_society_raises(self):
        wizard = WizardService.create_wizard(user=self.user)
        with self.assertRaises(ValidationError):
            FinancialYearSetupService.create_financial_year(
                wizard, "2026-27", user=self.user
            )

    def test_create_financial_year_creates_periods(self):
        fy = FinancialYearSetupService.create_financial_year(
            self.wizard, "2026-27", user=self.user
        )
        periods = AccountingPeriod.objects.filter(financial_year=fy)
        self.assertEqual(periods.count(), 12)

    def test_create_financial_year_stores_in_wizard_data(self):
        FinancialYearSetupService.create_financial_year(
            self.wizard, "2026-27", user=self.user
        )
        self.wizard.refresh_from_db()
        self.assertEqual(
            self.wizard.wizard_data.get("accounting_start_year"), "2026-27"
        )
        self.assertEqual(
            self.wizard.wizard_data.get("fy_pattern"), FY_PATTERN_APRIL_MARCH
        )
        self.assertIn("financial_year_id", self.wizard.wizard_data)

    def test_create_financial_year_creates_audit_log(self):
        FinancialYearSetupService.create_financial_year(
            self.wizard, "2026-27", user=self.user
        )
        self.assertTrue(
            MigrationAuditLog.objects.filter(
                wizard=self.wizard, action="CREATE_FINANCIAL_YEAR"
            ).exists()
        )

    def test_create_financial_year_jul_jun_pattern(self):
        WizardService.update_wizard_data(self.wizard, "fy_pattern", "JUL_JUN")
        fy = FinancialYearSetupService.create_financial_year(
            self.wizard, "2026-27", user=self.user
        )
        self.assertEqual(fy.start_date, date(2026, 7, 1))
        self.assertEqual(fy.end_date, date(2027, 6, 30))

    def test_create_financial_year_reopens_closed_fy(self):
        """If the FY was previously closed, create_financial_year reopens it."""
        fy = FinancialYearSetupService.create_financial_year(
            self.wizard, "2026-27", user=self.user
        )
        fy.is_open = False
        fy.save(update_fields=["is_open"])
        fy2 = FinancialYearSetupService.create_financial_year(
            self.wizard, "2026-27", user=self.user
        )
        self.assertTrue(fy2.is_open)

    def test_derive_fy_dates_invalid_pattern_raises(self):
        with self.assertRaises(ValidationError):
            FinancialYearSetupService.derive_fy_dates(
                "2026-27", "INVALID_PATTERN"
            )

    def test_get_financial_year_pattern_from_wizard_data(self):
        WizardService.update_wizard_data(self.wizard, "fy_pattern", "JAN_DEC")
        pattern = FinancialYearSetupService.get_financial_year_pattern(
            self.society
        )
        self.assertEqual(pattern, FY_PATTERN_JAN_DEC)

    def test_get_fy_options_returns_five_labels(self):
        opts = FinancialYearSetupService.get_fy_options(
            FY_PATTERN_APRIL_MARCH, reference_year=2026
        )
        self.assertEqual(len(opts), 5)
        self.assertIn("2026-27", opts)
        self.assertIn("2024-25", opts)
        self.assertIn("2028-29", opts)

    def test_get_fy_options_jan_dec(self):
        opts = FinancialYearSetupService.get_fy_options(
            FY_PATTERN_JAN_DEC, reference_year=2026
        )
        self.assertEqual(len(opts), 5)
        self.assertIn("2026-27", opts)
