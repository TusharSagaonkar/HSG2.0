from django.test import TestCase
from django.core.exceptions import ValidationError
from datetime import date
from accounting.models import (
    Voucher,
    Account,
    AccountCategory,
    LedgerEntry,
)


class VoucherTest(TestCase):

    def setUp(self):
        self.asset_cat = AccountCategory.objects.create(
            name="Cash",
            account_type="ASSET",
        )
        self.income_cat = AccountCategory.objects.create(
            name="Maintenance Income",
            account_type="INCOME",
        )

        self.cash = Account.objects.create(
            name="Cash",
            category=self.asset_cat,
        )
        self.income = Account.objects.create(
            name="Maintenance Income",
            category=self.income_cat,
        )

    def test_cannot_post_without_entries(self):
        v = Voucher.objects.create(
            voucher_type="GENERAL",
            voucher_date=date.today(),
        )
        with self.assertRaises(ValidationError):
            v.post()

    def test_cannot_post_unbalanced_voucher(self):
        v = Voucher.objects.create(
            voucher_type="GENERAL",
            voucher_date=date.today(),
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
            voucher_type="GENERAL",
            voucher_date=date.today(),
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
