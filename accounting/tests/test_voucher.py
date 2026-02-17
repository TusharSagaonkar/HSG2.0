from django.test import TestCase
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from datetime import date
from datetime import timedelta
from django.utils import timezone
from accounting.models import (
    Voucher,
    Account,
    AccountCategory,
    AccountingPeriod,
    FinancialYear,
    LedgerEntry,
)
from housing.models import Society


class VoucherTest(TestCase):

    def setUp(self):
        self.society = Society.objects.create(name="Test Society")
        self.voucher_date = date(2024, 8, 6)

        self.financial_year = FinancialYear.objects.create(
            society=self.society,
            name="FY 2024-25",
            start_date=date(2024, 4, 1),
            end_date=date(2025, 3, 31),
            is_open=True,
        )
        AccountingPeriod.objects.filter(
            society=self.society,
            financial_year=self.financial_year,
            start_date=date(2024, 8, 1),
            end_date=date(2024, 8, 31),
        ).update(is_open=True)

        self.asset_cat, _ = AccountCategory.objects.get_or_create(
            society=self.society,
            name="Cash",
            account_type="ASSET",
        )
        self.income_cat, _ = AccountCategory.objects.get_or_create(
            society=self.society,
            name="Maintenance Income",
            account_type="INCOME",
        )

        self.cash = Account.objects.create(
            society=self.society,
            name="Cash",
            category=self.asset_cat,
        )
        self.income = Account.objects.create(
            society=self.society,
            name="Maintenance Income",
            category=self.income_cat,
        )

    def test_cannot_post_without_entries(self):
        v = Voucher.objects.create(
            society=self.society,
            voucher_type="GENERAL",
            voucher_date=self.voucher_date,
        )
        with self.assertRaises(ValidationError):
            v.post()

    def test_cannot_post_with_single_entry(self):
        v = Voucher.objects.create(
            society=self.society,
            voucher_type="GENERAL",
            voucher_date=self.voucher_date,
        )
        LedgerEntry.objects.create(
            voucher=v,
            account=self.cash,
            debit=1000,
        )
        with self.assertRaises(ValidationError):
            v.post()

    def test_cannot_post_unbalanced_voucher(self):
        v = Voucher.objects.create(
            society=self.society,
            voucher_type="GENERAL",
            voucher_date=self.voucher_date,
        )
        LedgerEntry.objects.create(
            voucher=v,
            account=self.cash,
            debit=1000,
        )
        with self.assertRaises(ValidationError):
            v.post()

    def test_post_balanced_voucher(self):
        v = Voucher.objects.create(
            society=self.society,
            voucher_type="GENERAL",
            voucher_date=self.voucher_date,
        )
        LedgerEntry.objects.create(
            voucher=v,
            account=self.cash,
            debit=1000,
        )
        LedgerEntry.objects.create(
            voucher=v,
            account=self.income,
            credit=1000,
        )

        v.post()
        self.assertIsNotNone(v.posted_at)

    def test_future_date_voucher_is_blocked(self):
        v = Voucher.objects.create(
            society=self.society,
            voucher_type="GENERAL",
            voucher_date=date(2099, 1, 1),
        )
        LedgerEntry.objects.create(voucher=v, account=self.cash, debit=1000)
        LedgerEntry.objects.create(voucher=v, account=self.income, credit=1000)
        with self.assertRaises(ValidationError):
            v.post()

    def test_posted_number_is_unique_per_society_and_voucher_type(self):
        posted_at = timezone.now()
        Voucher.objects.create(
            society=self.society,
            voucher_type="GENERAL",
            voucher_date=self.voucher_date,
            voucher_number=1,
            posted_at=posted_at,
        )
        with self.assertRaises(IntegrityError):
            Voucher.objects.create(
                society=self.society,
                voucher_type="GENERAL",
                voucher_date=self.voucher_date,
                voucher_number=1,
                posted_at=posted_at + timedelta(seconds=1),
            )
