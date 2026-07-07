from django.core.exceptions import ValidationError

from core.test_base import SocietyTestCase
from core.test_factories import SocietyFactory
from housing.models import Structure


class StructureModelTest(SocietyTestCase):
    def test_root_structure(self):
        building = Structure.objects.create(
            society=self.society,
            structure_type=Structure.StructureType.BUILDING,
            name="Building A",
        )
        self.assertIsNone(building.parent)

    def test_cross_society_parent_not_allowed(self):
        other = SocietyFactory(name="Test Society Beta")

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
