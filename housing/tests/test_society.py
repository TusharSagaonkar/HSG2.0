from django.test import TestCase
from housing.models import Society


class SocietyModelTest(TestCase):
    def test_create_society(self):
        society = Society.objects.create(name="Green Heights")
        self.assertEqual(str(society), "Green Heights")
