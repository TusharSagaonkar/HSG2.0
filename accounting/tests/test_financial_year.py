from django.test import TestCase
from django.core.exceptions import ValidationError
from accounting.models import FinancialYear
from datetime import date
from django.utils import timezone
from housing.models import Society


class FinancialYearTest(TestCase):
    def setUp(self):
        self.society = Society.objects.create(name="Test Society")

    def test_valid_financial_year(self):
        today = timezone.localdate()
        current_start_year = today.year if today.month >= 4 else today.year - 1
        next_start_year = current_start_year + 1

        fy = FinancialYear(
            society=self.society,
            name=f"FY {next_start_year}-{str(next_start_year + 1)[-2:]}",
            start_date=date(next_start_year, 4, 1),
            end_date=date(next_start_year + 1, 3, 31),
        )
        fy.full_clean()  # should not raise

    def test_invalid_date_range(self):
        fy = FinancialYear(
            society=self.society,
            name="Invalid FY",
            start_date=date(2026, 4, 1),
            end_date=date(2025, 3, 31),
        )
        with self.assertRaises(ValidationError):
            fy.full_clean()
