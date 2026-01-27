from django.test import TestCase
from django.core.exceptions import ValidationError
from accounting.models import FinancialYear
from datetime import date


class FinancialYearTest(TestCase):

    def test_valid_financial_year(self):
        fy = FinancialYear(
            name="FY 2025-26",
            start_date=date(2025, 4, 1),
            end_date=date(2026, 3, 31),
        )
        fy.full_clean()  # should not raise

    def test_invalid_date_range(self):
        fy = FinancialYear(
            name="Invalid FY",
            start_date=date(2026, 4, 1),
            end_date=date(2025, 3, 31),
        )
        with self.assertRaises(ValidationError):
            fy.full_clean()
