from django.test import TestCase
from housing.models import Society, Structure, Unit


class UnitModelTest(TestCase):
    def setUp(self):
        self.society = Society.objects.create(name="Green Heights")
        self.building = Structure.objects.create(
            society=self.society,
            structure_type=Structure.StructureType.BUILDING,
            name="Building A",
        )

    def test_unit_creation(self):
        unit = Unit.objects.create(
            structure=self.building,
            unit_type=Unit.UnitType.FLAT,
            identifier="101",
        )
        self.assertEqual(unit.identifier, "101")
