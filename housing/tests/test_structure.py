from django.test import TestCase
from django.core.exceptions import ValidationError
from housing.models import Society, Structure


class StructureModelTest(TestCase):
    def setUp(self):
        self.society = Society.objects.create(name="Green Heights")

    def test_root_structure(self):
        building = Structure.objects.create(
            society=self.society,
            structure_type=Structure.StructureType.BUILDING,
            name="Building A",
        )
        self.assertIsNone(building.parent)

    def test_cross_society_parent_not_allowed(self):
        other = Society.objects.create(name="Blue Heights")

        parent = Structure.objects.create(
            society=other,
            structure_type=Structure.StructureType.BUILDING,
            name="Other Building",
        )

        invalid = Structure(
            society=self.society,
            parent=parent,
            structure_type=Structure.StructureType.BUILDING,
            name="Invalid",
        )

        with self.assertRaises(ValidationError):
            invalid.full_clean()
