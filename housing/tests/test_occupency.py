from datetime import date
from django.test import TestCase
from django.core.exceptions import ValidationError
from django.contrib.auth import get_user_model

from housing.models import Society, Structure, Unit, UnitOccupancy

User = get_user_model()


class UnitOccupancyTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("user1")

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

    def test_vacant_unit_no_occupant(self):
        occ = UnitOccupancy(
            unit=self.unit,
            occupancy_type=UnitOccupancy.OccupancyType.VACANT,
            start_date=date.today(),
        )
        occ.full_clean()  # should not raise
