from core.test_base import SocietyTestCase


class SocietyModelTest(SocietyTestCase):
    def test_create_society(self):
        self.assertEqual(str(self.society), self.society.name)
