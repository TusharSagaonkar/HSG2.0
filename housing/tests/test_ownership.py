from datetime import date
from django.test import TestCase
from django.core.exceptions import ValidationError
from django.contrib.auth import get_user_model

from housing.models import Society, Structure, Unit, UnitOwnership

User = get_user_model()


class UnitOwnershipTest(TestCase):
    def setUp(self):
        self.user1 = User.objects.create_user("user1")
        self.user2 = User.objects.create_user("user2")

        self.society = Society.objects.create(name="Green Heights")
        self.building = Structure.objects.create(
            society=self.society,
            structure_type=Structure.StructureType.BUILDING,
            name="Building A",
        )
        self.unit = Unit.objects.create(
            structure=self.building,
            unit_type=Unit.UnitType.FLAT,
            identifier="101",
        )

    def test_single_primary_owner(self):
        UnitOwnership.objects.create(
            unit=self.unit,
            owner=self.user1,
            role=UnitOwnership.OwnershipRole.PRIMARY,
            start_date=date.today(),
        )

        duplicate = UnitOwnership(
            unit=self.unit,
            owner=self.user2,
            role=UnitOwnership.OwnershipRole.PRIMARY,
            start_date=date.today(),
        )

        with self.assertRaises(ValidationError):
            duplicate.full_clean()
