"""Smoke tests for SocietySetupService and FinancialYearSetupService.

These tests verify that the society setup and financial year creation
services work end-to-end against a real database.
"""

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounting.models import AccountingPeriod, FinancialYear
from members.models import Member, Structure, Unit
from onboarding.models import OnboardingWizard
from onboarding.services import (
    FinancialYearSetupService,
    SocietySetupService,
    WizardService,
)

User = get_user_model()


class SocietySetupServiceTest(TestCase):
    """Tests for SocietySetupService (Steps 1, 6, 7, 8)."""

    def setUp(self):
        self.user = User.objects.create(
            email="setup@example.com", name="Setup User", is_active=True
        )
        self.wizard = WizardService.create_wizard(user=self.user)

    def test_create_society(self):
        society_data = {
            "name": "Test Society",
            "registration_number": "REG123",
            "address": "123 Main St",
            "city": "Mumbai",
            "state": "Maharashtra",
            "country": "India",
            "pin_code": "400001",
            "pan": "ABCDE1234F",
            "email": "society@example.com",
            "phone": "9876543210",
            "financial_year_pattern": "APRIL_MARCH",
        }
        society = SocietySetupService.create_society(
            self.wizard, society_data, user=self.user
        )
        self.assertEqual(society.name, "Test Society")
        self.assertEqual(society.registration_number, "REG123")
        self.assertEqual(self.wizard.society_id, society.id)
        # Extra fields stored in wizard_data
        self.assertEqual(
            self.wizard.wizard_data.get("financial_year_pattern"), "APRIL_MARCH"
        )
        self.assertEqual(self.wizard.wizard_data.get("city"), "Mumbai")

    def test_create_structure(self):
        society = SocietySetupService.create_society(
            self.wizard,
            {"name": "Struct Society", "address": "Test"},
            user=self.user,
        )
        structures_data = [
            {"building_name": "Tower A", "wing_name": "Wing 1", "floor_number": 1},
            {"building_name": "Tower A", "wing_name": "Wing 1", "floor_number": 2},
            {"building_name": "Tower B"},
        ]
        structures = SocietySetupService.create_structure(
            self.wizard, structures_data, user=self.user
        )
        # Should create: Tower A (building), Wing 1, Floor 1, Floor 2, Tower B
        building_a = Structure.objects.filter(
            society=society, name="Tower A", parent__isnull=True
        ).first()
        self.assertIsNotNone(building_a)
        wing1 = Structure.objects.filter(
            society=society, name="Wing 1", parent=building_a
        ).first()
        self.assertIsNotNone(wing1)
        floor1 = Structure.objects.filter(
            society=society, name="1", parent=wing1
        ).first()
        self.assertIsNotNone(floor1)
        building_b = Structure.objects.filter(
            society=society, name="Tower B", parent__isnull=True
        ).first()
        self.assertIsNotNone(building_b)

    def test_create_units(self):
        society = SocietySetupService.create_society(
            self.wizard,
            {"name": "Unit Society", "address": "Test"},
            user=self.user,
        )
        SocietySetupService.create_structure(
            self.wizard,
            [{"building_name": "B1", "floor_number": 1}],
            user=self.user,
        )
        units_data = [
            {"flat_number": "101", "area": "850.00", "usage_type": "RESIDENTIAL", "building": "B1", "floor": 1},
            {"flat_number": "102", "area": "900.00", "usage_type": "COMMERCIAL", "building": "B1", "floor": 1},
        ]
        units = SocietySetupService.create_units(
            self.wizard, units_data, user=self.user
        )
        self.assertEqual(len(units), 2)
        unit101 = Unit.objects.filter(identifier="101").first()
        self.assertIsNotNone(unit101)
        self.assertEqual(unit101.unit_type, Unit.UnitType.FLAT)
        self.assertEqual(unit101.area_sqft, Decimal("850.00"))
        unit102 = Unit.objects.filter(identifier="102").first()
        self.assertIsNotNone(unit102)
        self.assertEqual(unit102.unit_type, Unit.UnitType.OFFICE)

    def test_assign_members(self):
        society = SocietySetupService.create_society(
            self.wizard,
            {"name": "Member Society", "address": "Test"},
            user=self.user,
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
        members_data = [
            {
                "member_name": "John Doe",
                "member_type": "OWNER",
                "unit_identifier": "101",
                "email": "john@example.com",
                "phone": "9876543210",
            },
        ]
        members = SocietySetupService.assign_members(
            self.wizard, members_data, user=self.user
        )
        self.assertEqual(len(members), 1)
        member = members[0]
        self.assertEqual(member.full_name, "John Doe")
        self.assertEqual(member.role, Member.MemberRole.OWNER)
        self.assertEqual(member.email, "john@example.com")


class FinancialYearSetupServiceTest(TestCase):
    """Tests for FinancialYearSetupService (Steps 4 & 5)."""

    def setUp(self):
        self.user = User.objects.create(
            email="fy@example.com", name="FY User", is_active=True
        )
        self.wizard = WizardService.create_wizard(user=self.user)
        # Create a society first (required for FY creation).
        self.society = SocietySetupService.create_society(
            self.wizard,
            {"name": "FY Society", "address": "Test"},
            user=self.user,
        )

    def test_create_financial_year_april_march(self):
        fy = FinancialYearSetupService.create_financial_year(
            self.wizard, "2026-27", user=self.user
        )
        self.assertEqual(fy.society_id, self.society.id)
        self.assertEqual(fy.start_date, date(2026, 4, 1))
        self.assertEqual(fy.end_date, date(2027, 3, 31))
        self.assertTrue(fy.is_open)
        # FinancialYear.save() auto-creates monthly periods.
        periods = AccountingPeriod.objects.filter(financial_year=fy)
        self.assertEqual(periods.count(), 12)

    def test_create_financial_year_jan_dec(self):
        # Set the FY pattern in wizard_data.
        WizardService.update_wizard_data(
            self.wizard, "fy_pattern", "JAN_DEC"
        )
        fy = FinancialYearSetupService.create_financial_year(
            self.wizard, "2026-27", user=self.user
        )
        self.assertEqual(fy.start_date, date(2026, 1, 1))
        self.assertEqual(fy.end_date, date(2026, 12, 31))

    def test_create_financial_year_idempotent(self):
        fy1 = FinancialYearSetupService.create_financial_year(
            self.wizard, "2026-27", user=self.user
        )
        fy2 = FinancialYearSetupService.create_financial_year(
            self.wizard, "2026-27", user=self.user
        )
        self.assertEqual(fy1.pk, fy2.pk)

    def test_get_financial_year_pattern_default(self):
        pattern = FinancialYearSetupService.get_financial_year_pattern(
            self.society
        )
        self.assertEqual(pattern, "APRIL_MARCH")

    def test_derive_fy_dates_all_patterns(self):
        sd, ed = FinancialYearSetupService.derive_fy_dates(
            "2026-27", "APRIL_MARCH"
        )
        self.assertEqual(sd, date(2026, 4, 1))
        self.assertEqual(ed, date(2027, 3, 31))

        sd, ed = FinancialYearSetupService.derive_fy_dates(
            "2026-27", "JAN_DEC"
        )
        self.assertEqual(sd, date(2026, 1, 1))
        self.assertEqual(ed, date(2026, 12, 31))

        sd, ed = FinancialYearSetupService.derive_fy_dates(
            "2026-27", "JUL_JUN"
        )
        self.assertEqual(sd, date(2026, 7, 1))
        self.assertEqual(ed, date(2027, 6, 30))

    def test_get_fy_options(self):
        opts = FinancialYearSetupService.get_fy_options(
            "APRIL_MARCH", reference_year=2026
        )
        self.assertEqual(len(opts), 5)
        self.assertIn("2026-27", opts)
